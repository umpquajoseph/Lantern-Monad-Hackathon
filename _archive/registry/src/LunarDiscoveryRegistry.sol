// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/// @title LunarDiscoveryRegistry
/// @notice A provenance registry for simulated lunar prospecting: who reported what about a
/// site, when, and the hash of the evidence. It is NOT a rover controller, and it does not
/// prove that ice exists or grant any claim to a resource. It proves that a record was
/// published, by an authorised rover, unchanged since.
///
/// Lifecycle of a site, one way only:
///   None -> Candidate            registerCandidate         (Surveyor)
///   Candidate -> SurveyorInspected submitSurveyorInspection (Surveyor, Positive/Negative/Inconclusive)
///   SurveyorInspected -> ProspectorAnalyzed submitProspectorAnalysis (Prospector)
///
/// Append-only: every submission adds a Report; nothing is ever edited or deleted. A
/// Prospector analysis names the report it follows (parentReportId), so the chain of
/// observations for a site can be walked from the chain alone.
///
/// Storage is minimal on purpose. Evidence (reports, measurements, routes) lives off-chain
/// as content-addressed JSON. On chain: its keccak256 (`evidenceHash`, which is also the
/// digest inside its CIDv1, multihash keccak-256) and the CID string itself.
contract LunarDiscoveryRegistry {
    enum Stage {
        None,
        Candidate,
        SurveyorInspected,
        ProspectorAnalyzed
    }

    enum Result {
        Unknown,
        Positive,
        Negative,
        Inconclusive
    }

    struct Site {
        bytes32 missionId;
        int64 xCm; // world frame (south polar stereographic), centimetres
        int64 yCm;
        uint16 confidenceBp; // Surveyor's candidate confidence, basis points (0..10000)
        Stage stage;
        Result surveyorResult;
        Result prospectorResult;
        address registeredBy;
        uint64 registeredAt;
        uint256 latestReportId;
    }

    struct Report {
        bytes32 siteId;
        Stage stage; // the stage this report moved the site to
        Result result;
        bytes32 evidenceHash; // keccak256 of the report JSON (canonical bytes)
        bytes32 routeHash; // keccak256 of the route JSON, or 0 for a candidate
        string reportCid;
        string routeCid;
        address submitter;
        uint64 time;
        uint256 parentReportId; // 0 = none; report ids start at 1
    }

    /// @notice World coordinates are limited to +-2000 km of the pole (2e8 cm): the lunar
    /// south polar stereographic window any LOLA product here uses.
    int64 public constant MAX_ABS_COORD_CM = 200_000_000;
    uint256 public constant MAX_CID_LENGTH = 100;

    address public admin;
    mapping(address => bool) public isSurveyor;
    mapping(address => bool) public isProspector;

    mapping(bytes32 => Site) private _sites;
    bytes32[] private _siteIds;
    Report[] private _reports; // index 0 is a placeholder so ids start at 1

    event SubmitterSet(address indexed who, bytes32 indexed role, bool allowed);
    event CandidateRegistered(
        bytes32 indexed siteId, bytes32 indexed missionId, address indexed submitter,
        uint256 reportId, int64 xCm, int64 yCm, uint16 confidenceBp, bytes32 evidenceHash, string reportCid
    );
    event SurveyorInspectionSubmitted(
        bytes32 indexed siteId, Result indexed result, address indexed submitter,
        uint256 reportId, uint256 parentReportId, bytes32 evidenceHash, string reportCid, string routeCid
    );
    event ProspectorAnalysisSubmitted(
        bytes32 indexed siteId, Result indexed result, address indexed submitter,
        uint256 reportId, uint256 parentReportId, bytes32 evidenceHash, string reportCid, string routeCid
    );

    error NotAdmin();
    error NotSurveyor();
    error NotProspector();
    error SiteExists(bytes32 siteId);
    error NoSuchSite(bytes32 siteId);
    error BadTransition(Stage from, Stage to);
    error BadCoordinates(int64 xCm, int64 yCm);
    error BadConfidence(uint16 confidenceBp);
    error EmptyHash();
    error BadCid();
    error BadResult();
    error BadParent(uint256 given, uint256 expected);
    error NoSuchReport(uint256 reportId);

    bytes32 public constant SURVEYOR = keccak256("SURVEYOR");
    bytes32 public constant PROSPECTOR = keccak256("PROSPECTOR");

    constructor() {
        admin = msg.sender;
        _reports.push(); // placeholder: report ids start at 1
    }

    // ------------------------------------------------------------------ admin

    modifier onlyAdmin() {
        if (msg.sender != admin) revert NotAdmin();
        _;
    }

    function setSurveyor(address who, bool allowed) external onlyAdmin {
        isSurveyor[who] = allowed;
        emit SubmitterSet(who, SURVEYOR, allowed);
    }

    function setProspector(address who, bool allowed) external onlyAdmin {
        isProspector[who] = allowed;
        emit SubmitterSet(who, PROSPECTOR, allowed);
    }

    // ---------------------------------------------------------------- reports

    function registerCandidate(
        bytes32 siteId,
        bytes32 missionId,
        int64 xCm,
        int64 yCm,
        uint16 confidenceBp,
        bytes32 evidenceHash,
        string calldata reportCid
    ) external returns (uint256 reportId) {
        if (!isSurveyor[msg.sender]) revert NotSurveyor();
        if (_sites[siteId].stage != Stage.None) revert SiteExists(siteId);
        if (xCm > MAX_ABS_COORD_CM || xCm < -MAX_ABS_COORD_CM || yCm > MAX_ABS_COORD_CM || yCm < -MAX_ABS_COORD_CM) {
            revert BadCoordinates(xCm, yCm);
        }
        if (confidenceBp > 10_000) revert BadConfidence(confidenceBp);
        _checkRefs(evidenceHash, reportCid);

        reportId = _append(siteId, Stage.Candidate, Result.Unknown, evidenceHash, bytes32(0), reportCid, "", 0);
        _sites[siteId] = Site({
            missionId: missionId,
            xCm: xCm,
            yCm: yCm,
            confidenceBp: confidenceBp,
            stage: Stage.Candidate,
            surveyorResult: Result.Unknown,
            prospectorResult: Result.Unknown,
            registeredBy: msg.sender,
            registeredAt: uint64(block.timestamp),
            latestReportId: reportId
        });
        _siteIds.push(siteId);
        emit CandidateRegistered(siteId, missionId, msg.sender, reportId, xCm, yCm, confidenceBp, evidenceHash, reportCid);
    }

    function submitSurveyorInspection(
        bytes32 siteId,
        Result result,
        bytes32 evidenceHash,
        string calldata reportCid,
        bytes32 routeHash,
        string calldata routeCid
    ) external returns (uint256 reportId) {
        if (!isSurveyor[msg.sender]) revert NotSurveyor();
        Site storage s = _site(siteId);
        if (s.stage != Stage.Candidate) revert BadTransition(s.stage, Stage.SurveyorInspected);
        if (result == Result.Unknown) revert BadResult();
        _checkRefs(evidenceHash, reportCid);
        _checkRefs(routeHash, routeCid); // an inspection must carry its completed route

        uint256 parent = s.latestReportId;
        reportId = _append(siteId, Stage.SurveyorInspected, result, evidenceHash, routeHash, reportCid, routeCid, parent);
        s.stage = Stage.SurveyorInspected;
        s.surveyorResult = result;
        s.latestReportId = reportId;
        emit SurveyorInspectionSubmitted(siteId, result, msg.sender, reportId, parent, evidenceHash, reportCid, routeCid);
    }

    function submitProspectorAnalysis(
        bytes32 siteId,
        Result result,
        bytes32 evidenceHash,
        string calldata reportCid,
        bytes32 routeHash,
        string calldata routeCid,
        uint256 parentReportId
    ) external returns (uint256 reportId) {
        if (!isProspector[msg.sender]) revert NotProspector();
        Site storage s = _site(siteId);
        if (s.stage != Stage.SurveyorInspected) revert BadTransition(s.stage, Stage.ProspectorAnalyzed);
        if (result == Result.Unknown) revert BadResult();
        if (parentReportId != s.latestReportId) revert BadParent(parentReportId, s.latestReportId);
        _checkRefs(evidenceHash, reportCid);
        _checkRefs(routeHash, routeCid);

        reportId = _append(siteId, Stage.ProspectorAnalyzed, result, evidenceHash, routeHash, reportCid, routeCid, parentReportId);
        s.stage = Stage.ProspectorAnalyzed;
        s.prospectorResult = result;
        s.latestReportId = reportId;
        emit ProspectorAnalysisSubmitted(siteId, result, msg.sender, reportId, parentReportId, evidenceHash, reportCid, routeCid);
    }

    // ------------------------------------------------------------------ reads

    function getSite(bytes32 siteId) external view returns (Site memory) {
        return _site(siteId);
    }

    /// @return stage the site's lifecycle stage
    /// @return result the latest result: the Prospector's once analysed, else the Surveyor's
    /// @return latestReportId the report that set it
    function getLatestSiteStatus(bytes32 siteId) external view returns (Stage stage, Result result, uint256 latestReportId) {
        Site storage s = _site(siteId);
        stage = s.stage;
        result = s.stage == Stage.ProspectorAnalyzed ? s.prospectorResult : s.surveyorResult;
        latestReportId = s.latestReportId;
    }

    function getReport(uint256 reportId) external view returns (Report memory) {
        if (reportId == 0 || reportId >= _reports.length) revert NoSuchReport(reportId);
        return _reports[reportId];
    }

    function reportCount() external view returns (uint256) {
        return _reports.length - 1;
    }

    function siteCount() external view returns (uint256) {
        return _siteIds.length;
    }

    /// @notice Site ids in registration order, [start, start + count).
    function getSiteIds(uint256 start, uint256 count) external view returns (bytes32[] memory ids) {
        uint256 n = _siteIds.length;
        if (start > n) start = n;
        uint256 end = start + count > n ? n : start + count;
        ids = new bytes32[](end - start);
        for (uint256 i = start; i < end; i++) ids[i - start] = _siteIds[i];
    }

    // --------------------------------------------------------------- internal

    function _site(bytes32 siteId) internal view returns (Site storage s) {
        s = _sites[siteId];
        if (s.stage == Stage.None) revert NoSuchSite(siteId);
    }

    function _checkRefs(bytes32 hash_, string calldata cid) internal pure {
        if (hash_ == bytes32(0)) revert EmptyHash();
        bytes calldata b = bytes(cid);
        // A base32 CIDv1 starts with "b" and is about 59 characters here.
        if (b.length < 10 || b.length > MAX_CID_LENGTH || b[0] != "b") revert BadCid();
    }

    function _append(
        bytes32 siteId,
        Stage stage,
        Result result,
        bytes32 evidenceHash,
        bytes32 routeHash,
        string calldata reportCid,
        string memory routeCid,
        uint256 parent
    ) internal returns (uint256 id) {
        id = _reports.length;
        _reports.push(
            Report({
                siteId: siteId,
                stage: stage,
                result: result,
                evidenceHash: evidenceHash,
                routeHash: routeHash,
                reportCid: reportCid,
                routeCid: routeCid,
                submitter: msg.sender,
                time: uint64(block.timestamp),
                parentReportId: parent
            })
        );
    }
}
