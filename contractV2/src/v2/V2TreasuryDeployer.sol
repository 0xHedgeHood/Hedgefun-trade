// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {BoundDeployer} from "../HedgeFunDeployers.sol";
import {HedgeFunTreasuryBase} from "../HedgeFunTreasuryBase.sol";
import {IUniswapV3Pool} from "../interfaces/IUniswapV3.sol";
import {HedgeFunV2Treasury} from "./HedgeFunV2Treasury.sol";

interface IFactoryOwner { function owner() external view returns (address); }

contract V2InitCodeChunk {
    constructor(bytes memory data) {
        assembly ("memory-safe") { return(add(data, 0x20), mload(data)) }
    }
}

/// @notice Deploys one V2 treasury per launch, choosing its code by STRATEGY KIND. Kind 0 is `HedgeFunV2Treasury`.
///
/// The factory has no bytes to spare under EIP-170 and its `Request` is the deployed V1 ABI, so the per-launch strategy choice
/// lives here instead: a creator names the kind for their own (symbol, nonce) salt before `predict`/`launch`, and the
/// factory's unchanged `deploy(salt, args)` picks that kind's code. Nothing about the factory moves; a second strategy
/// is one `registerKind` by the factory owner, never a new factory or hook.
///
/// Every kind must accept the same constructor arguments as `HedgeFunV2Treasury` and honour the treasury surface the
/// factory, hook, vault and routers call (`wire`, `setLiquidityVault`, `creditLiquidityFee`, `book`, `poolKey`,
/// `health`). Kinds are write-once: a launched treasury's code is part of its CREATE2 address and cannot be swapped.
contract V2TreasuryDeployer is BoundDeployer {
    error TreasuryDeployFailed();
    error StopInsideExecutionFriction(uint256 stopBps, uint256 minimumExclusiveBps);
    error BadKind();
    error NotOwner();
    error BadLpBps();

    struct Kind { address chunkA; address chunkB; }
    /// registered strategy code, by kind. Index 0 is `HedgeFunV2Treasury`.
    Kind[] private _kinds;
    /// @notice the kind a creator chose for a salt; unset = kind 0
    mapping(bytes32 => uint8) public strategyKindOf;

    event KindRegistered(uint8 indexed kind, address chunkA, address chunkB);
    event StrategyKindSet(address indexed creator, string symbol, uint96 nonce, uint8 kind);
    event LpBpsSet(address indexed stock, uint16 lpBps);

    /// @notice the share of a graduating curve's REAL stock reserve that seeds the V4 pool; the rest is this
    ///         treasury's strategy capital. Per stock, set by the factory owner for FUTURE launches, in the
    ///         launch terms, and frozen per treasury when the launch deploys it. Neither factory nor curve
    ///         deployer has the bytes for it.
    uint16 public constant DEFAULT_LP_BPS = 5000;
    uint16 public constant MIN_LP_BPS = 1000;
    mapping(address => uint16) private _lpBps;
    /// @notice the LP share a launched treasury's curve graduates with
    mapping(address => uint16) public lpBpsOfTreasury;

    function lpBps(address stock) public view returns (uint16) {
        uint16 value = _lpBps[stock];
        return value == 0 ? DEFAULT_LP_BPS : value;
    }
    /// @notice Changes future launches only; a pending quote for `stock` becomes stale (`Restated`).
    function setLpBps(address stock, uint16 value) external {
        _onlyOwner();
        if (value < MIN_LP_BPS || value > 10000) revert BadLpBps();
        _lpBps[stock] = value;
        emit LpBpsSet(stock, value);
    }
    function _onlyOwner() private view {
        if (factory == address(0) || msg.sender != IFactoryOwner(factory).owner()) revert NotOwner();
    }

    constructor() {
        (address a, address b) = makeChunks(type(HedgeFunV2Treasury).creationCode);
        _kinds.push(Kind(a, b));
        emit KindRegistered(0, a, b);
    }

    /// @notice Split creation code into two immutable code blobs: a treasury's initcode alone exceeds what one
    ///         contract may hold. Anyone may call; the blobs are inert until `registerKind` names them.
    function makeChunks(bytes memory code) public returns (address a, address b) {
        uint256 half = code.length / 2;
        a = address(new V2InitCodeChunk(_slice(code, 0, half)));
        b = address(new V2InitCodeChunk(_slice(code, half, code.length - half)));
    }

    function _slice(bytes memory src, uint256 offset, uint256 len) private pure returns (bytes memory part) {
        part = new bytes(len);
        assembly ("memory-safe") {
            mcopy(add(part, 0x20), add(add(src, 0x20), offset), len)
        }
    }

    function version() external pure returns (uint256) { return 2; }
    function kindCount() external view returns (uint256) { return _kinds.length; }
    function kinds(uint8 kind) external view returns (address chunkA_, address chunkB_) {
        if (kind >= _kinds.length) revert BadKind();
        Kind storage k = _kinds[kind];
        return (k.chunkA, k.chunkB);
    }
    function chunkA() external view returns (address) { return _kinds[0].chunkA; }
    function chunkB() external view returns (address) { return _kinds[0].chunkB; }

    /// @notice The bound factory's owner adds a strategy kind for FUTURE launches. Existing kinds never change.
    function registerKind(address a, address b) external returns (uint8 kind) {
        _onlyOwner();
        if (a.code.length == 0 || b.code.length == 0 || _kinds.length == type(uint8).max) revert BadKind();
        kind = uint8(_kinds.length);
        _kinds.push(Kind(a, b));
        emit KindRegistered(kind, a, b);
    }

    /// @notice A creator picks the strategy for their own upcoming launch: the salt is (symbol, msg.sender, nonce),
    ///         exactly as the factory derives it, so nobody can choose for anyone else. Changing it after `predict`
    ///         moves the treasury address and the launch reverts `Restated` -- re-quote. Kind 0 needs no call.
    function setStrategyKind(string calldata symbol, uint96 nonce, uint8 kind) external {
        if (kind >= _kinds.length) revert BadKind();
        strategyKindOf[keccak256(abi.encode(symbol, msg.sender, nonce))] = kind;
        emit StrategyKindSet(msg.sender, symbol, nonce, kind);
    }

    function _code(bytes32 salt, bytes calldata args) private view returns (bytes memory code) {
        Kind storage k = _kinds[strategyKindOf[salt]];
        address a = k.chunkA;
        address b = k.chunkB;
        uint256 alen = a.code.length;
        uint256 blen = b.code.length;
        code = new bytes(alen + blen + args.length);
        assembly ("memory-safe") {
            let dst := add(code, 0x20)
            extcodecopy(a, dst, 0, alen)
            extcodecopy(b, add(dst, alen), 0, blen)
            calldatacopy(add(add(dst, alen), blen), args.offset, args.length)
        }
    }

    function _validate(bytes calldata args) private view {
        (,, address v3Pool,,,,, HedgeFunTreasuryBase.Params memory p) = abi.decode(args,
            (address, address, address, address, address, address, address, HedgeFunTreasuryBase.Params));
        uint256 friction = p.maxSlippageBps + uint256(IUniswapV3Pool(v3Pool).fee()) / 100 + p.bountyBps;
        if (p.stopBps != 0 && p.stopBps <= friction) revert StopInsideExecutionFriction(p.stopBps, friction);
    }

    function deploy(bytes32 salt, bytes calldata args) external returns (address a) {
        _onlyFactory();
        _validate(args);
        bytes memory code = _code(salt, args);
        assembly ("memory-safe") { a := create2(0, add(code, 0x20), mload(code), salt) }
        if (a == address(0)) revert TreasuryDeployFailed();
        // `args` starts (usdg, stock, ...): the stock is its second word
        lpBpsOfTreasury[a] = lpBps(address(uint160(uint256(bytes32(args[32:64])))));
    }

    function predict(bytes32 salt, bytes calldata args) external view returns (address) {
        _validate(args);
        return _at(salt, keccak256(_code(salt, args)));
    }
}
