// SPDX-License-Identifier: Apache-2.0
pragma solidity ^0.8.28;

/// The two calls LanternSurvey makes on the Lantern contract (../contracts/src/Lantern.sol).
interface ILantern {
    struct Find {
        address agency;
        int64 xCm;
        int64 yCm;
        uint64 sigmaCm;
        uint32 reading;
        bytes32 dataHash;
        uint64 time;
    }

    enum Status {
        Reported,
        Confirmed,
        Disputed
    }

    function isAgency(address a) external view returns (bool);

    function getFind(uint256 id)
        external
        view
        returns (Find memory find, address[] memory agree, address[] memory disagree, Status status);
}

/// @title LanternSurvey: what a rover's run found, posted next to Lantern's finds.
/// @notice Lantern holds where each ice find is, how sure the rover is of that spot, and
/// other agencies' checks. LanternSurvey adds the rest of a run's results:
///   - an Assay per find: how much ice (wt% and a modelled mass) and which volatiles,
///     posted only by the agency that made the find, once per find;
///   - a Traverse per rover per run: the path it believes it drove (downsampled on chain,
///     the full path committed by hash), with its worst position uncertainty;
///   - a Run record: keccak256 of the run's whole results file, so anyone holding
///     data/runs/<run_id>/results.json can re-hash it and compare.
/// Writers are Lantern's agencies; nothing is edited or deleted.
///
/// Units: positions whole centimetres (world frame, LOLA south polar stereographic
/// metres x 100), latE6/lonE6 microdegrees, iceBp hundredths of wt% water-equivalent,
/// iceMassKg kilograms, areaM2 square metres, depthCm centimetres, volatilesPpm parts per
/// million by mass of regolith, species named by volatileName(i). Mass, area, depth and
/// volatiles come from an off-chain model (sim/results.py); every one of its parameters
/// is named and sourced or marked ASSUMED there.
contract LanternSurvey {
    uint256 public constant N_VOLATILES = 8;
    uint256 public constant MAX_WAYPOINTS = 512;

    struct AssayInput {
        uint256 findId; // the Lantern find this assay describes
        int32 latE6;
        int32 lonE6;
        uint32 iceBp;
        uint64 iceMassKg;
        uint32 areaM2;
        uint32 depthCm;
        bytes32 runId;
        bytes32 dataHash; // keccak256 of the canonical assay JSON
        uint32[8] volatilesPpm;
    }

    struct Assay {
        uint256 findId;
        address agency;
        int32 latE6;
        int32 lonE6;
        uint32 iceBp;
        uint64 iceMassKg;
        uint32 areaM2;
        uint32 depthCm;
        uint64 time;
        bytes32 runId;
        bytes32 dataHash;
        uint32[8] volatilesPpm;
    }

    struct Traverse {
        address agency;
        uint32 nFull; // points in the full path behind pathHash
        uint32 maxSigmaCm; // worst 1-sigma position uncertainty along the path
        uint64 lengthCm; // length of the full path
        uint64 time;
        bytes32 runId;
        bytes32 pathHash; // keccak256 of the canonical full-path JSON
        int32[] xsCm; // downsampled path
        int32[] ysCm;
    }

    struct Run {
        address poster;
        uint32 nFinds;
        uint64 totalIceMassKg;
        uint64 time;
        bytes32 runId;
        bytes32 resultsHash;
        string label;
    }

    ILantern public immutable lantern;
    string[8] private _volatileNames;

    Assay[] private _assays;
    mapping(uint256 => uint256) private _assayOfFind; // find id -> assay index + 1
    Traverse[] private _traverses;
    Run[] private _runs;
    mapping(bytes32 => uint256) private _runOf; // run id -> run index + 1

    event AssayPosted(
        uint256 indexed assayId,
        uint256 indexed findId,
        address indexed agency,
        uint32 iceBp,
        uint64 iceMassKg,
        bytes32 runId
    );
    event TraversePosted(
        uint256 indexed traverseId, address indexed agency, bytes32 indexed runId, bytes32 pathHash
    );
    event RunPosted(
        uint256 indexed runIndex, bytes32 indexed runId, address indexed poster, bytes32 resultsHash
    );

    error NotAnAgency(address caller);
    error NotFinder(uint256 findId, address finder, address caller);
    error AlreadyAssayed(uint256 findId);
    error NoSuchAssay(uint256 id);
    error NoSuchTraverse(uint256 id);
    error NoSuchRun(bytes32 runId);
    error RunExists(bytes32 runId);
    error BadWaypoints(uint256 xs, uint256 ys);

    constructor(address lantern_, string[8] memory volatileNames_) {
        lantern = ILantern(lantern_);
        _volatileNames = volatileNames_;
    }

    modifier onlyAgency() {
        if (!lantern.isAgency(msg.sender)) revert NotAnAgency(msg.sender);
        _;
    }

    // ------------------------------------------------------------------ writes

    /// @notice How much ice and which volatiles at a Lantern find. Finder only, once.
    /// Reverts NoSuchFind (from Lantern) if the find does not exist.
    function postAssay(AssayInput calldata a) external onlyAgency returns (uint256 id) {
        (ILantern.Find memory f,,,) = lantern.getFind(a.findId);
        if (f.agency != msg.sender) revert NotFinder(a.findId, f.agency, msg.sender);
        if (_assayOfFind[a.findId] != 0) revert AlreadyAssayed(a.findId);
        id = _assays.length;
        _assays.push(
            Assay({
                findId: a.findId,
                agency: msg.sender,
                latE6: a.latE6,
                lonE6: a.lonE6,
                iceBp: a.iceBp,
                iceMassKg: a.iceMassKg,
                areaM2: a.areaM2,
                depthCm: a.depthCm,
                time: uint64(block.timestamp),
                runId: a.runId,
                dataHash: a.dataHash,
                volatilesPpm: a.volatilesPpm
            })
        );
        _assayOfFind[a.findId] = id + 1;
        emit AssayPosted(id, a.findId, msg.sender, a.iceBp, a.iceMassKg, a.runId);
    }

    /// @notice The path this rover believes it drove in a run.
    function postTraverse(
        bytes32 runId,
        bytes32 pathHash,
        uint32 nFull,
        uint64 lengthCm,
        uint32 maxSigmaCm,
        int32[] calldata xsCm,
        int32[] calldata ysCm
    ) external onlyAgency returns (uint256 id) {
        if (xsCm.length != ysCm.length || xsCm.length < 2 || xsCm.length > MAX_WAYPOINTS) {
            revert BadWaypoints(xsCm.length, ysCm.length);
        }
        id = _traverses.length;
        Traverse storage t = _traverses.push();
        t.agency = msg.sender;
        t.nFull = nFull;
        t.maxSigmaCm = maxSigmaCm;
        t.lengthCm = lengthCm;
        t.time = uint64(block.timestamp);
        t.runId = runId;
        t.pathHash = pathHash;
        t.xsCm = xsCm;
        t.ysCm = ysCm;
        emit TraversePosted(id, msg.sender, runId, pathHash);
    }

    /// @notice Commit a run's whole results file. One record per run id.
    function postRun(
        bytes32 runId,
        bytes32 resultsHash,
        uint32 nFinds,
        uint64 totalIceMassKg,
        string calldata label
    ) external onlyAgency returns (uint256 index) {
        if (_runOf[runId] != 0) revert RunExists(runId);
        index = _runs.length;
        _runs.push(
            Run({
                poster: msg.sender,
                nFinds: nFinds,
                totalIceMassKg: totalIceMassKg,
                time: uint64(block.timestamp),
                runId: runId,
                resultsHash: resultsHash,
                label: label
            })
        );
        _runOf[runId] = index + 1;
        emit RunPosted(index, runId, msg.sender, resultsHash);
    }

    // ------------------------------------------------------------------- reads

    function volatileNames() external view returns (string[8] memory) {
        return _volatileNames;
    }

    function assayCount() external view returns (uint256) {
        return _assays.length;
    }

    function getAssay(uint256 id) external view returns (Assay memory) {
        if (id >= _assays.length) revert NoSuchAssay(id);
        return _assays[id];
    }

    /// @return found false when the find has no assay yet.
    function assayOfFind(uint256 findId) external view returns (bool found, Assay memory assay) {
        uint256 i = _assayOfFind[findId];
        if (i == 0) return (false, assay);
        return (true, _assays[i - 1]);
    }

    function getAssays(uint256 start, uint256 count) external view returns (Assay[] memory out) {
        uint256 n = _assays.length;
        if (start >= n) return new Assay[](0);
        if (count > n - start) count = n - start;
        out = new Assay[](count);
        for (uint256 i = 0; i < count; i++) {
            out[i] = _assays[start + i];
        }
    }

    function traverseCount() external view returns (uint256) {
        return _traverses.length;
    }

    function getTraverse(uint256 id) external view returns (Traverse memory) {
        if (id >= _traverses.length) revert NoSuchTraverse(id);
        return _traverses[id];
    }

    function runCount() external view returns (uint256) {
        return _runs.length;
    }

    function getRunAt(uint256 index) external view returns (Run memory) {
        return _runs[index];
    }

    function getRun(bytes32 runId) external view returns (Run memory) {
        uint256 i = _runOf[runId];
        if (i == 0) revert NoSuchRun(runId);
        return _runs[i - 1];
    }
}
