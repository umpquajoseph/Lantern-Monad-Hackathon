// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import {LunarDiscoveryRegistry as R} from "../src/LunarDiscoveryRegistry.sol";

/// Foundry cheatcodes, declared here so the tests need no extra library.
interface Vm {
    function prank(address) external;
    function expectRevert(bytes calldata) external;
    function expectEmit(bool, bool, bool, bool) external;
    function warp(uint256) external;
}

contract LunarDiscoveryRegistryTest {
    Vm constant vm = Vm(address(uint160(uint256(keccak256("hevm cheat code")))));

    R reg;
    address constant SURVEYOR = address(0x5001);
    address constant PROSPECTOR = address(0xB002);
    address constant STRANGER = address(0xBAD);

    bytes32 constant SITE = keccak256("mission-1/T07");
    bytes32 constant MISSION = keccak256("mission-1");
    bytes32 constant H1 = keccak256("candidate report");
    bytes32 constant H2 = keccak256("inspection report");
    bytes32 constant H3 = keccak256("analysis report");
    bytes32 constant RH = keccak256("route");
    string constant CID1 = "bafkrwidrslhhsmcfo3ei6ufqwrrppainufows52smf2uwgjn2w3j3onvhe";
    string constant CID2 = "bafkrwieaztxerx465haaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
    string constant RCID = "bafkrwibl3svhgcmlxl4ictabibmdej6bxfnyz64eatgxpvlawbca2d2hpq";

    // Mirrors of the registry's events, for expectEmit.
    event CandidateRegistered(
        bytes32 indexed siteId, bytes32 indexed missionId, address indexed submitter,
        uint256 reportId, int64 xCm, int64 yCm, uint16 confidenceBp, bytes32 evidenceHash, string reportCid
    );
    event SurveyorInspectionSubmitted(
        bytes32 indexed siteId, R.Result indexed result, address indexed submitter,
        uint256 reportId, uint256 parentReportId, bytes32 evidenceHash, string reportCid, string routeCid
    );
    event ProspectorAnalysisSubmitted(
        bytes32 indexed siteId, R.Result indexed result, address indexed submitter,
        uint256 reportId, uint256 parentReportId, bytes32 evidenceHash, string reportCid, string routeCid
    );

    function setUp() public {
        reg = new R();
        reg.setSurveyor(SURVEYOR, true);
        reg.setProspector(PROSPECTOR, true);
        vm.warp(1_800_000_000);
    }

    function _candidate() internal returns (uint256) {
        vm.prank(SURVEYOR);
        return reg.registerCandidate(SITE, MISSION, 7337187, 3617673, 8200, H1, CID1);
    }

    function _inspect(R.Result r) internal returns (uint256) {
        vm.prank(SURVEYOR);
        return reg.submitSurveyorInspection(SITE, r, H2, CID2, RH, RCID);
    }

    // --------------------------------------------------------- happy path

    function test_fullLifecycle_linksReports() public {
        uint256 c = _candidate();
        uint256 i = _inspect(R.Result.Positive);
        vm.prank(PROSPECTOR);
        uint256 a = reg.submitProspectorAnalysis(SITE, R.Result.Positive, H3, CID1, RH, RCID, i);

        require(c == 1 && i == 2 && a == 3, "report ids start at 1 and count up");
        R.Report memory rc = reg.getReport(c);
        R.Report memory ri = reg.getReport(i);
        R.Report memory ra = reg.getReport(a);
        require(ri.parentReportId == c && ra.parentReportId == i, "each report names its parent");
        require(rc.evidenceHash == H1 && ri.evidenceHash == H2 && ra.evidenceHash == H3, "hashes stored as given");
        require(ri.routeHash == RH && keccak256(bytes(ri.routeCid)) == keccak256(bytes(RCID)), "route ref stored");
        require(ra.submitter == PROSPECTOR && ri.submitter == SURVEYOR, "submitters recorded");

        (R.Stage st, R.Result res, uint256 latest) = reg.getLatestSiteStatus(SITE);
        require(st == R.Stage.ProspectorAnalyzed && res == R.Result.Positive && latest == a, "latest status");
        R.Site memory s = reg.getSite(SITE);
        require(s.surveyorResult == R.Result.Positive && s.prospectorResult == R.Result.Positive, "both results kept");
        require(s.xCm == 7337187 && s.yCm == 3617673 && s.confidenceBp == 8200, "site fields");
        require(reg.siteCount() == 1 && reg.reportCount() == 3, "counts");
        bytes32[] memory ids = reg.getSiteIds(0, 10);
        require(ids.length == 1 && ids[0] == SITE, "site enumeration");
    }

    function test_negativeInspection_isRecorded() public {
        _candidate();
        uint256 i = _inspect(R.Result.Negative);
        (R.Stage st, R.Result res,) = reg.getLatestSiteStatus(SITE);
        require(st == R.Stage.SurveyorInspected && res == R.Result.Negative, "negative kept on record");
        require(reg.getReport(i).result == R.Result.Negative, "negative report stored");
    }

    function test_inconclusiveInspection_isRecorded() public {
        _candidate();
        _inspect(R.Result.Inconclusive);
        (, R.Result res,) = reg.getLatestSiteStatus(SITE);
        require(res == R.Result.Inconclusive, "inconclusive kept");
    }

    // ------------------------------------------------------------- events

    function test_events_carryHashesAndCids() public {
        vm.expectEmit(true, true, true, true);
        emit CandidateRegistered(SITE, MISSION, SURVEYOR, 1, 7337187, 3617673, 8200, H1, CID1);
        _candidate();

        vm.expectEmit(true, true, true, true);
        emit SurveyorInspectionSubmitted(SITE, R.Result.Positive, SURVEYOR, 2, 1, H2, CID2, RCID);
        _inspect(R.Result.Positive);

        vm.expectEmit(true, true, true, true);
        emit ProspectorAnalysisSubmitted(SITE, R.Result.Negative, PROSPECTOR, 3, 2, H3, CID1, RCID);
        vm.prank(PROSPECTOR);
        reg.submitProspectorAnalysis(SITE, R.Result.Negative, H3, CID1, RH, RCID, 2);
    }

    // ------------------------------------------------------- access control

    function test_unauthorized_cannotRegister() public {
        vm.expectRevert(abi.encodeWithSelector(R.NotSurveyor.selector));
        vm.prank(STRANGER);
        reg.registerCandidate(SITE, MISSION, 0, 0, 100, H1, CID1);
    }

    function test_prospector_cannotSubmitSurveyorRecords() public {
        _candidate();
        vm.expectRevert(abi.encodeWithSelector(R.NotSurveyor.selector));
        vm.prank(PROSPECTOR);
        reg.submitSurveyorInspection(SITE, R.Result.Positive, H2, CID2, RH, RCID);
    }

    function test_surveyor_cannotSubmitAnalysis() public {
        _candidate();
        _inspect(R.Result.Positive);
        vm.expectRevert(abi.encodeWithSelector(R.NotProspector.selector));
        vm.prank(SURVEYOR);
        reg.submitProspectorAnalysis(SITE, R.Result.Positive, H3, CID1, RH, RCID, 2);
    }

    function test_onlyAdmin_setsSubmitters_andCanRevoke() public {
        vm.expectRevert(abi.encodeWithSelector(R.NotAdmin.selector));
        vm.prank(STRANGER);
        reg.setSurveyor(STRANGER, true);

        reg.setSurveyor(SURVEYOR, false);
        vm.expectRevert(abi.encodeWithSelector(R.NotSurveyor.selector));
        vm.prank(SURVEYOR);
        reg.registerCandidate(SITE, MISSION, 0, 0, 100, H1, CID1);
    }

    // ------------------------------------------------------------ validation

    function test_duplicateSiteId_reverts() public {
        _candidate();
        vm.expectRevert(abi.encodeWithSelector(R.SiteExists.selector, SITE));
        _candidate();
    }

    function test_invalidCoordinates_revert() public {
        int64 tooFar = 200_000_001;
        vm.expectRevert(abi.encodeWithSelector(R.BadCoordinates.selector, tooFar, int64(0)));
        vm.prank(SURVEYOR);
        reg.registerCandidate(SITE, MISSION, tooFar, 0, 100, H1, CID1);
    }

    function test_badConfidence_reverts() public {
        vm.expectRevert(abi.encodeWithSelector(R.BadConfidence.selector, uint16(10_001)));
        vm.prank(SURVEYOR);
        reg.registerCandidate(SITE, MISSION, 0, 0, 10_001, H1, CID1);
    }

    function test_emptyHash_andMalformedCid_revert() public {
        vm.expectRevert(abi.encodeWithSelector(R.EmptyHash.selector));
        vm.prank(SURVEYOR);
        reg.registerCandidate(SITE, MISSION, 0, 0, 100, bytes32(0), CID1);

        vm.expectRevert(abi.encodeWithSelector(R.BadCid.selector));
        vm.prank(SURVEYOR);
        reg.registerCandidate(SITE, MISSION, 0, 0, 100, H1, "");

        vm.expectRevert(abi.encodeWithSelector(R.BadCid.selector));
        vm.prank(SURVEYOR);
        reg.registerCandidate(SITE, MISSION, 0, 0, 100, H1, "Qmnotbase32cidv1xxxxxxxx");
    }

    function test_inspectionWithoutRoute_reverts() public {
        _candidate();
        vm.expectRevert(abi.encodeWithSelector(R.EmptyHash.selector));
        vm.prank(SURVEYOR);
        reg.submitSurveyorInspection(SITE, R.Result.Positive, H2, CID2, bytes32(0), RCID);
    }

    function test_unknownResult_reverts() public {
        _candidate();
        vm.expectRevert(abi.encodeWithSelector(R.BadResult.selector));
        _inspect(R.Result.Unknown);
    }

    function test_noSuchSite_reverts() public {
        vm.expectRevert(abi.encodeWithSelector(R.NoSuchSite.selector, SITE));
        reg.getSite(SITE);
    }

    // ------------------------------------------------------------- transitions

    function test_cannotAnalyse_beforeInspection() public {
        _candidate();
        vm.expectRevert(abi.encodeWithSelector(R.BadTransition.selector, R.Stage.Candidate, R.Stage.ProspectorAnalyzed));
        vm.prank(PROSPECTOR);
        reg.submitProspectorAnalysis(SITE, R.Result.Positive, H3, CID1, RH, RCID, 1);
    }

    function test_cannotInspectTwice() public {
        _candidate();
        _inspect(R.Result.Positive);
        vm.expectRevert(abi.encodeWithSelector(R.BadTransition.selector, R.Stage.SurveyorInspected, R.Stage.SurveyorInspected));
        _inspect(R.Result.Negative);
    }

    function test_cannotAnalyseTwice_appendOnly() public {
        _candidate();
        _inspect(R.Result.Positive);
        vm.prank(PROSPECTOR);
        reg.submitProspectorAnalysis(SITE, R.Result.Positive, H3, CID1, RH, RCID, 2);
        vm.expectRevert(abi.encodeWithSelector(R.BadTransition.selector, R.Stage.ProspectorAnalyzed, R.Stage.ProspectorAnalyzed));
        vm.prank(PROSPECTOR);
        reg.submitProspectorAnalysis(SITE, R.Result.Negative, H3, CID1, RH, RCID, 3);
        // the first analysis is untouched
        require(reg.getReport(3).result == R.Result.Positive, "history not overwritten");
    }

    function test_analysis_mustNameLatestReport() public {
        _candidate();
        _inspect(R.Result.Positive);
        vm.expectRevert(abi.encodeWithSelector(R.BadParent.selector, uint256(1), uint256(2)));
        vm.prank(PROSPECTOR);
        reg.submitProspectorAnalysis(SITE, R.Result.Positive, H3, CID1, RH, RCID, 1);
    }

    function test_noSuchReport_reverts() public {
        vm.expectRevert(abi.encodeWithSelector(R.NoSuchReport.selector, uint256(0)));
        reg.getReport(0);
    }
}
