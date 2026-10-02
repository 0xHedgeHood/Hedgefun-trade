// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {Test} from "forge-std/Test.sol";
import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {Math} from "@openzeppelin/contracts/utils/math/Math.sol";
import {TickMath} from "v4-core/src/libraries/TickMath.sol";
import {PriceOracle} from "../src/PriceOracle.sol";
import {PoolTrader} from "../src/PoolTrader.sol";
import {EarnVaultV3SwapAdapter} from "../src/options/EarnVaultV3SwapAdapter.sol";
import {MockToken, MockFeed, MockPool, AlwaysOpen, ISwapCallback} from "./mocks/Mocks.sol";

/// @dev Exercises the exact-input short-fill behavior; PoolTrader's separate tests cover the real swap math.
contract EarnHalfFillPool {
    address public immutable token0;
    address public immutable token1;
    uint24 public constant fee = 3000;
    uint16 public constant cardinality = 1000;
    uint256 private constant SCALE = 1e30;
    uint256 private constant PRICE = 100e18;
    int24 private immutable _tick;
    uint160 private immutable _sqrt;

    constructor(address usdg, address stock) {
        token0 = usdg;
        token1 = stock;
        _sqrt = uint160(Math.sqrt(Math.mulDiv(SCALE, 1 << 192, PRICE)));
        _tick = TickMath.getTickAtSqrtPrice(_sqrt);
    }

    function slot0() external view returns (uint160, int24, uint16, uint16, uint16, uint8, bool) {
        return (_sqrt, _tick, 0, cardinality, cardinality, 0, true);
    }

    function observe(uint32[] calldata ago) external view returns (int56[] memory tc, uint160[] memory l) {
        tc = new int56[](2);
        l = new uint160[](2);
        tc[1] = int56(_tick) * int56(uint56(ago[0]));
    }

    function swap(address recipient, bool zeroForOne, int256 amountSpecified, uint160 limit, bytes calldata data)
        external
        returns (int256 a0, int256 a1)
    {
        require(zeroForOne && amountSpecified > 0 && limit < _sqrt, "wrong side");
        uint256 used = uint256(amountSpecified) / 2;
        uint256 got = Math.mulDiv(used * (1e6 - fee) / 1e6, SCALE, PRICE);
        IERC20(token1).transfer(recipient, got);
        a0 = int256(used);
        a1 = -int256(got);
        ISwapCallback(msg.sender).uniswapV3SwapCallback(a0, a1, data);
    }
}

contract EarnVaultV3SwapAdapterTest is Test {
    uint256 private constant P = 100e18;
    uint256 private constant SCALE = 1e30;
    MockToken private usdg;
    MockToken private stock;
    MockFeed private stockFeed;
    MockFeed private usdgFeed;
    PriceOracle private oracle;
    MockPool private pool;
    EarnVaultV3SwapAdapter private adapter;

    function setUp() public {
        vm.warp(1_700_000_000);
        usdg = new MockToken("USDG", 6);
        stock = new MockToken("RHNVDA", 18);
        stockFeed = new MockFeed(8);
        usdgFeed = new MockFeed(8);
        stockFeed.set(100e8);
        usdgFeed.set(1e8);
        oracle = new PriceOracle(
            address(stock), address(stockFeed), address(usdgFeed), address(new AlwaysOpen()), 26 hours, 26 hours
        );
        pool = new MockPool(address(stock), address(usdg), false, 3000, SCALE);
        pool.setPrice(P);
        stock.mint(address(pool), 1000 ether);
        usdg.mint(address(this), 100_000e6);
        adapter = _deploy(address(pool), 50, 100);
        usdg.approve(address(adapter), type(uint256).max);
    }

    function _deploy(address p, uint16 deviation, uint16 slippage) internal returns (EarnVaultV3SwapAdapter) {
        return new EarnVaultV3SwapAdapter(
            address(this), address(usdg), address(stock), p, address(oracle), deviation, slippage
        );
    }

    function test_buyFullFillAndDeliverExactBalances() public {
        uint256 usdgBefore = usdg.balanceOf(address(this));
        (uint256 spent, uint256 received) = adapter.buy(1000e6, 9 ether);
        assertEq(spent, 1000e6);
        assertEq(received, 9.97 ether);
        assertEq(usdg.balanceOf(address(this)), usdgBefore - spent);
        assertEq(stock.balanceOf(address(this)), received);
        assertEq(usdg.balanceOf(address(adapter)), 0);
        assertEq(stock.balanceOf(address(adapter)), 0);
    }

    function test_refundUnspentInputAndPreservePreexistingDust() public {
        EarnHalfFillPool halfPool = new EarnHalfFillPool(address(usdg), address(stock));
        stock.mint(address(halfPool), 1000 ether);
        EarnVaultV3SwapAdapter half = _deploy(address(halfPool), 50, 100);
        usdg.approve(address(half), type(uint256).max);
        usdg.mint(address(half), 3e6);
        stock.mint(address(half), 1 ether);

        uint256 usdgBefore = usdg.balanceOf(address(this));
        uint256 stockBefore = stock.balanceOf(address(this));
        (uint256 spent, uint256 received) = half.buy(1000e6, 4 ether);
        assertEq(spent, 500e6);
        assertEq(received, 4.985 ether);
        assertEq(usdg.balanceOf(address(this)), usdgBefore - spent);
        assertEq(stock.balanceOf(address(this)), stockBefore + received);
        assertEq(usdg.balanceOf(address(half)), 3e6);
        assertEq(stock.balanceOf(address(half)), 1 ether);
    }

    function test_onlyVaultMayBuy() public {
        vm.prank(address(0xBEEF));
        vm.expectRevert(EarnVaultV3SwapAdapter.NotVault.selector);
        adapter.buy(1000e6, 1);
    }

    function test_zeroInputAndMinimumOutputFail() public {
        vm.expectRevert(EarnVaultV3SwapAdapter.InvalidAmount.selector);
        adapter.buy(0, 1);
        vm.expectRevert(EarnVaultV3SwapAdapter.InvalidAmount.selector);
        adapter.buy(1000e6, 0);
        vm.expectRevert(EarnVaultV3SwapAdapter.InsufficientOutput.selector);
        adapter.buy(1000e6, 10 ether);
        assertEq(stock.balanceOf(address(this)), 0);
    }

    function test_unhealthyOracleOrPoolFailsClosed() public {
        vm.warp(block.timestamp + 27 hours);
        vm.expectRevert(EarnVaultV3SwapAdapter.UnhealthyMarket.selector);
        adapter.buy(1000e6, 1 ether);

        stockFeed.set(100e8);
        usdgFeed.set(1e8);
        pool.setPrice(110e18);
        vm.expectRevert(EarnVaultV3SwapAdapter.UnhealthyMarket.selector);
        adapter.buy(1000e6, 1 ether);

        pool.setPrice(P);
        pool.pushSpot(P, 200);
        vm.expectRevert(EarnVaultV3SwapAdapter.UnhealthyMarket.selector);
        adapter.buy(1000e6, 1 ether);
    }

    function test_constructorRejectsUnsafeBandsAndShortRing() public {
        vm.expectRevert(PoolTrader.BadConfig.selector);
        _deploy(address(pool), 100, 100);
        vm.expectRevert(PoolTrader.BadConfig.selector);
        _deploy(address(pool), 50, 301);
        pool.setCardinality(659);
        vm.expectRevert(abi.encodeWithSelector(PoolTrader.ShortObservationRing.selector, uint16(659), uint256(660)));
        _deploy(address(pool), 50, 100);
    }

    function test_otherTokenOrderingAlsoBuys() public {
        MockPool flipped = new MockPool(address(stock), address(usdg), true, 3000, SCALE);
        flipped.setPrice(P);
        stock.mint(address(flipped), 1000 ether);
        EarnVaultV3SwapAdapter another = _deploy(address(flipped), 50, 100);
        usdg.approve(address(another), type(uint256).max);
        (uint256 spent, uint256 received) = another.buy(1000e6, 9 ether);
        assertEq(spent, 1000e6);
        assertEq(received, 9.97 ether);
    }
}
