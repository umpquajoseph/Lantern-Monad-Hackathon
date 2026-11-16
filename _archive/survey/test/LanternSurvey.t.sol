// SPDX-License-Identifier: Apache-2.0
pragma solidity ^0.8.28;

import {Test} from "forge-std/Test.sol";
import {Lantern} from "lantern/Lantern.sol";
import {LanternSurvey} from "../src/LanternSurvey.sol";

/// Survey rules: only Lantern's agencies write; only the finder assays its own find, once;
/// a run id is committed once; traverses need matching, non-trivial waypoint arrays.
contract LanternSurveyTest is Test {
    Lantern lantern;
    LanternSurvey survey;
    address A = makeAddr("agencyA");
    address B = makeAddr("agencyB");
    address X = makeAddr("stranger");

    function setUp() public {
        address[] memory ags = new address[](2);
        ags[0] = A;
        ags[1] = B;
        string[] memory names = new string[](2);
        names[0] = "Agency A";
        names[1] = "Agency B";
        lantern = new Lantern(ags, names);
        survey = new LanternSurvey(
            address(lantern), ["H2S", "NH3", "SO2", "C2H4", "CO2", "CH3OH", "CH4", "OH"]
        );
    }

    function _findByA() internal returns (uint256 id) {
        vm.prank(A);
        id = lantern.postFind(7_335_000, 3_610_000, 300, 560, keccak256("raw"));
    }

    function _assay(uint256 findId) internal pure returns (LanternSurvey.AssayInput memory a) {
        a.findId = findId;
        a.latE6 = -88_100_000;
        a.lonE6 = 63_800_000;
        a.iceBp = 560;
        a.iceMassKg = 1_234;
        a.areaM2 = 78;
        a.depthCm = 100;
        a.runId = keccak256("run1");
        a.dataHash = keccak256("assay");
        a.volatilesPpm = [uint32(10_600), 1_830, 3_960, 1_090, 1_890, 1_100, 230, 10];
    }

    function test_FinderAssaysAndReadsBack() public {
        uint256 fid = _findByA();
        vm.prank(A);
        uint256 id = survey.postAssay(_assay(fid));
        LanternSurvey.Assay memory a = survey.getAssay(id);
        assertEq(a.findId, fid);
        assertEq(a.agency, A);
        assertEq(a.iceMassKg, 1_234);
        assertEq(a.volatilesPpm[0], 10_600);
        (bool found, LanternSurvey.Assay memory b) = survey.assayOfFind(fid);
        assertTrue(found);
        assertEq(b.areaM2, 78);
        assertEq(survey.volatileNames()[2], "SO2");
        assertEq(survey.getAssays(0, 10).length, 1);
    }

    function test_OnlyFinderMayAssay() public {
        uint256 fid = _findByA();
        vm.prank(B);
        vm.expectRevert(abi.encodeWithSelector(LanternSurvey.NotFinder.selector, fid, A, B));
        survey.postAssay(_assay(fid));
    }

    function test_AssayOnce() public {
        uint256 fid = _findByA();
        vm.startPrank(A);
        survey.postAssay(_assay(fid));
        vm.expectRevert(abi.encodeWithSelector(LanternSurvey.AlreadyAssayed.selector, fid));
        survey.postAssay(_assay(fid));
        vm.stopPrank();
    }

    function test_AssayNeedsARealFind() public {
        vm.prank(A);
        vm.expectRevert(); // Lantern's NoSuchFind bubbles up
        survey.postAssay(_assay(42));
    }

    function test_StrangerRefused() public {
        uint256 fid = _findByA();
        vm.prank(X);
        vm.expectRevert(abi.encodeWithSelector(LanternSurvey.NotAnAgency.selector, X));
        survey.postAssay(_assay(fid));
    }

    function test_TraverseRoundTrip() public {
        int32[] memory xs = new int32[](3);
        int32[] memory ys = new int32[](3);
        (xs[0], xs[1], xs[2]) = (int32(7_325_000), int32(7_330_000), int32(7_335_000));
        (ys[0], ys[1], ys[2]) = (int32(3_600_000), int32(3_605_000), int32(3_610_000));
        vm.prank(B);
        uint256 id = survey.postTraverse(keccak256("run1"), keccak256("path"), 900, 141_421, 250, xs, ys);
        LanternSurvey.Traverse memory t = survey.getTraverse(id);
        assertEq(t.agency, B);
        assertEq(t.nFull, 900);
        assertEq(t.maxSigmaCm, 250);
        assertEq(t.xsCm[2], 7_335_000);
        assertEq(survey.traverseCount(), 1);
    }

    function test_TraverseShapeChecked() public {
        int32[] memory xs = new int32[](2);
        int32[] memory ys = new int32[](3);
        vm.prank(A);
        vm.expectRevert(abi.encodeWithSelector(LanternSurvey.BadWaypoints.selector, 2, 3));
        survey.postTraverse(bytes32(0), bytes32(0), 0, 0, 0, xs, ys);
        int32[] memory one = new int32[](1);
        vm.prank(A);
        vm.expectRevert(abi.encodeWithSelector(LanternSurvey.BadWaypoints.selector, 1, 1));
        survey.postTraverse(bytes32(0), bytes32(0), 0, 0, 0, one, one);
    }

    function test_RunCommittedOnce() public {
        bytes32 rid = keccak256("run1");
        vm.prank(A);
        survey.postRun(rid, keccak256("results"), 2, 2_468, "Run 1: crater fixes on");
        LanternSurvey.Run memory r = survey.getRun(rid);
        assertEq(r.poster, A);
        assertEq(r.totalIceMassKg, 2_468);
        assertEq(r.label, "Run 1: crater fixes on");
        vm.prank(B);
        vm.expectRevert(abi.encodeWithSelector(LanternSurvey.RunExists.selector, rid));
        survey.postRun(rid, keccak256("other"), 0, 0, "x");
    }
}
