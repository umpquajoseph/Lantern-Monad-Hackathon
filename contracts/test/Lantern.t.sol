// SPDX-License-Identifier: Apache-2.0
pragma solidity ^0.8.28;

import {Test} from "forge-std/Test.sol";
import {Lantern} from "../src/Lantern.sol";

/// Tests for the checking rules in docs/spec.html, with the demo's own numbers.
contract LanternTest is Test {
    Lantern lantern;
    address A = makeAddr("agencyA");
    address B = makeAddr("agencyB");
    address C = makeAddr("agencyC");
    address stranger = makeAddr("stranger");

    // A spot in the world frame, in whole centimetres; y negative to exercise signed maths.
    int64 constant X = 12_345;
    int64 constant Y = -6_789;
    // docs/spec.html, the demo: run 1, A's find is ±3 m; run 2, A is about 20 m unsure.
    uint64 constant SIGMA_3M = 300;
    uint64 constant SIGMA_20M = 2000;
    // 5.60 wt%: LCROSS at Cabeus, as cited in the PROSPECT team brief (via 4 specs.md).
    uint32 constant READING = 560;
    bytes32 constant HASH = keccak256("canonical raw readings json");

    function setUp() public {
        address[] memory addrs = new address[](3);
        addrs[0] = A;
        addrs[1] = B;
        addrs[2] = C;
        string[] memory names = new string[](3);
        names[0] = "Agency A";
        names[1] = "Agency B";
        names[2] = "Agency C";
        lantern = new Lantern(addrs, names);
    }

    function _post(address who, uint64 sigma) internal returns (uint256) {
        vm.prank(who);
        return lantern.postFind(X, Y, sigma, READING, HASH);
    }

    // ------------------------------------------------------------ constructor

    function test_constructor_registersAgenciesInOrder() public view {
        (address[] memory addrs, string[] memory names) = lantern.agencies();
        assertEq(addrs.length, 3);
        assertEq(addrs[0], A);
        assertEq(addrs[1], B);
        assertEq(names[1], "Agency B");
        assertTrue(lantern.isAgency(C));
        assertFalse(lantern.isAgency(stranger));
        assertEq(lantern.agencyCount(), 3);
    }

    function test_constructor_rejectsOneAgency() public {
        address[] memory addrs = new address[](1);
        addrs[0] = A;
        string[] memory names = new string[](1);
        vm.expectRevert(Lantern.BadAgencies.selector);
        new Lantern(addrs, names);
    }

    function test_constructor_rejectsMismatchedNames() public {
        address[] memory addrs = new address[](2);
        addrs[0] = A;
        addrs[1] = B;
        string[] memory names = new string[](1);
        vm.expectRevert(Lantern.BadAgencies.selector);
        new Lantern(addrs, names);
    }

    function test_constructor_rejectsZeroAndDuplicateAddresses() public {
        string[] memory names = new string[](2);
        address[] memory addrs = new address[](2);
        addrs[0] = A;
        addrs[1] = address(0);
        vm.expectRevert(Lantern.BadAgencies.selector);
        new Lantern(addrs, names);
        addrs[1] = A;
        vm.expectRevert(Lantern.BadAgencies.selector);
        new Lantern(addrs, names);
    }

    // --------------------------------------------------------------- postFind

    function test_postFind_storesEmitsAndStartsReported() public {
        vm.expectEmit(true, true, false, true);
        emit Lantern.FindPosted(0, A, X, Y, SIGMA_3M, READING, HASH);
        uint256 id = _post(A, SIGMA_3M);
        assertEq(id, 0);
        assertEq(lantern.findCount(), 1);

        (Lantern.Find memory f, address[] memory agree, address[] memory disagree, Lantern.Status s) =
            lantern.getFind(0);
        assertEq(f.agency, A);
        assertEq(f.xCm, X);
        assertEq(f.yCm, Y);
        assertEq(f.sigmaCm, SIGMA_3M);
        assertEq(f.reading, READING);
        assertEq(f.dataHash, HASH);
        assertEq(f.time, block.timestamp);
        assertEq(agree.length, 0);
        assertEq(disagree.length, 0);
        assertEq(uint8(s), uint8(Lantern.Status.Reported));
    }

    function test_postFind_idsIncrement() public {
        assertEq(_post(A, SIGMA_3M), 0);
        assertEq(_post(B, SIGMA_3M), 1);
        assertEq(_post(A, SIGMA_20M), 2);
        assertEq(lantern.findCount(), 3);
    }

    function test_postFind_strangerRefused() public {
        vm.prank(stranger);
        vm.expectRevert(abi.encodeWithSelector(Lantern.NotAnAgency.selector, stranger));
        lantern.postFind(X, Y, SIGMA_3M, READING, HASH);
    }

    function test_getFind_unknownIdRefused() public {
        vm.expectRevert(abi.encodeWithSelector(Lantern.NoSuchFind.selector, 0));
        lantern.getFind(0);
    }

    // -------------------------------------------------------- checkFind rules

    function test_checkFind_ownFindRefused() public {
        uint256 id = _post(A, SIGMA_3M);
        vm.prank(A);
        vm.expectRevert(abi.encodeWithSelector(Lantern.OwnFind.selector, id, A));
        lantern.checkFind(id, X, Y, SIGMA_3M, READING, true);
    }

    function test_checkFind_strangerRefused() public {
        uint256 id = _post(A, SIGMA_3M);
        vm.prank(stranger);
        vm.expectRevert(abi.encodeWithSelector(Lantern.NotAnAgency.selector, stranger));
        lantern.checkFind(id, X, Y, SIGMA_3M, READING, true);
    }

    function test_checkFind_unknownFindRefused() public {
        vm.prank(B);
        vm.expectRevert(abi.encodeWithSelector(Lantern.NoSuchFind.selector, 7));
        lantern.checkFind(7, X, Y, SIGMA_3M, READING, true);
    }

    /// Run 1 of the demo: A posts at ±3 m, B measures there, the ice is there, confirmed.
    function test_checkFind_run1_agreeConfirms() public {
        uint256 id = _post(A, SIGMA_3M);
        vm.prank(B);
        vm.expectEmit(true, true, true, true);
        emit Lantern.FindChecked(
            id, 0, B, true, X + 100, Y - 100, SIGMA_3M, READING, Lantern.Status.Confirmed
        );
        uint256 checkId = lantern.checkFind(id, X + 100, Y - 100, SIGMA_3M, READING, true);
        assertEq(checkId, 0);

        (, address[] memory agree, address[] memory disagree, Lantern.Status s) = lantern.getFind(id);
        assertEq(agree.length, 1);
        assertEq(agree[0], B);
        assertEq(disagree.length, 0);
        assertEq(uint8(s), uint8(Lantern.Status.Confirmed));
        assertTrue(lantern.hasChecked(id, B));

        Lantern.Check memory c = lantern.getCheck(0);
        assertEq(c.findId, id);
        assertEq(c.agency, B);
        assertEq(c.xCm, X + 100);
        assertTrue(c.agrees);
        uint256[] memory ids = lantern.checksOf(id);
        assertEq(ids.length, 1);
        assertEq(ids[0], 0);
    }

    /// Run 2 of the demo: A is 20 m unsure, B measures at the reported spot and finds
    /// nothing. The check is in range (3 × (20 m + 3 m) = 69 m) and the find is disputed.
    function test_checkFind_run2_disagreeDisputes() public {
        uint256 id = _post(A, SIGMA_20M);
        vm.prank(B);
        lantern.checkFind(id, X, Y, SIGMA_3M, 0, false);
        (, address[] memory agree, address[] memory disagree, Lantern.Status s) = lantern.getFind(id);
        assertEq(agree.length, 0);
        assertEq(disagree.length, 1);
        assertEq(disagree[0], B);
        assertEq(uint8(s), uint8(Lantern.Status.Disputed));
    }

    function test_checkFind_oneDisagreementOutweighsAgreements() public {
        uint256 id = _post(A, SIGMA_3M);
        vm.prank(B);
        lantern.checkFind(id, X, Y, SIGMA_3M, READING, true);
        assertEq(uint8(lantern.statusOf(id)), uint8(Lantern.Status.Confirmed));
        vm.prank(C);
        lantern.checkFind(id, X, Y, SIGMA_3M, 0, false);
        assertEq(uint8(lantern.statusOf(id)), uint8(Lantern.Status.Disputed));
        (, address[] memory agree, address[] memory disagree,) = lantern.getFind(id);
        assertEq(agree.length, 1);
        assertEq(disagree.length, 1);
        assertEq(lantern.checkCount(), 2);
    }

    function test_checkFind_secondCheckBySameAgencyRefused() public {
        uint256 id = _post(A, SIGMA_3M);
        vm.prank(B);
        lantern.checkFind(id, X, Y, SIGMA_3M, READING, true);
        vm.prank(B);
        vm.expectRevert(abi.encodeWithSelector(Lantern.AlreadyChecked.selector, id, B));
        lantern.checkFind(id, X, Y, SIGMA_3M, 0, false);
    }

    /// Allowed distance is 3 × (300 + 300) = 1800 cm. Exactly on the limit is accepted.
    function test_checkFind_atRangeLimitAccepted() public {
        uint256 id = _post(A, SIGMA_3M);
        vm.prank(B);
        lantern.checkFind(id, X + 1800, Y, SIGMA_3M, READING, true);
        assertEq(uint8(lantern.statusOf(id)), uint8(Lantern.Status.Confirmed));
    }

    function test_checkFind_oneCmPastLimitRefused() public {
        uint256 id = _post(A, SIGMA_3M);
        vm.prank(B);
        vm.expectRevert(
            abi.encodeWithSelector(Lantern.TooFar.selector, id, uint256(1801) * 1801, uint256(1800) * 1800)
        );
        lantern.checkFind(id, X, Y - 1801, SIGMA_3M, READING, true);
    }

    /// The rule is on straight-line distance, not per axis: 1300² + 1300² > 1800².
    function test_checkFind_diagonalUsesEuclideanDistance() public {
        uint256 id = _post(A, SIGMA_3M);
        vm.prank(B);
        lantern.checkFind(id, X + 1200, Y + 1200, SIGMA_3M, READING, true); // 1697 cm, ok
        uint256 id2 = _post(A, SIGMA_3M);
        vm.prank(B);
        vm.expectRevert(
            abi.encodeWithSelector(
                Lantern.TooFar.selector, id2, uint256(2) * 1300 * 1300, uint256(1800) * 1800
            )
        );
        lantern.checkFind(id2, X + 1300, Y + 1300, SIGMA_3M, READING, true); // 1838 cm, too far
    }

    /// A precise find cannot be checked by a rover that is itself lost: with the checker at
    /// σ = 20 m the allowed distance grows to 69 m, so the *finder's* precision is what a
    /// small circle of trust depends on. Both sigmas count, as the spec says.
    function test_checkFind_checkerUncertaintyWidensRange() public {
        uint256 id = _post(A, SIGMA_3M);
        vm.prank(B);
        lantern.checkFind(id, X + 6900, Y, SIGMA_20M, READING, true);
        uint256 id2 = _post(A, SIGMA_3M);
        vm.prank(B);
        vm.expectRevert(
            abi.encodeWithSelector(Lantern.TooFar.selector, id2, uint256(6901) * 6901, uint256(6900) * 6900)
        );
        lantern.checkFind(id2, X + 6901, Y, SIGMA_20M, READING, true);
    }

    struct Pair {
        int64 fx;
        int64 fy;
        uint64 fs;
        int64 cx;
        int64 cy;
        uint64 cs;
    }

    function testFuzz_withinCheckRange_isSymmetricAndNeverReverts(Pair memory p) public view {
        (bool ok1, uint256 d1, uint256 a1) = lantern.withinCheckRange(p.fx, p.fy, p.fs, p.cx, p.cy, p.cs);
        (bool ok2, uint256 d2, uint256 a2) = lantern.withinCheckRange(p.cx, p.cy, p.cs, p.fx, p.fy, p.fs);
        assertEq(ok1, ok2);
        assertEq(d1, d2);
        assertEq(a1, a2);
        assertEq(ok1, d1 <= a1);
    }

    function test_withinCheckRange_extremeCoordinatesDoNotOverflow() public view {
        (bool ok, uint256 d2,) = lantern.withinCheckRange(
            type(int64).min, type(int64).min, 0, type(int64).max, type(int64).max, 0
        );
        assertFalse(ok);
        // |dx| = 2^64 - 1, so d2 = 2 * (2^64 - 1)^2.
        uint256 span = uint256(uint64(type(uint64).max));
        assertEq(d2, 2 * span * span);
    }

    // ----------------------------------------------------------------- paging

    function test_getFinds_pagesAndClamps() public {
        _post(A, SIGMA_3M);
        _post(B, SIGMA_3M);
        _post(A, SIGMA_20M);
        vm.prank(B);
        lantern.checkFind(2, X, Y, SIGMA_3M, 0, false);

        (Lantern.Find[] memory finds,, address[][] memory disagree, Lantern.Status[] memory statuses) =
            lantern.getFinds(0, 100);
        assertEq(finds.length, 3);
        assertEq(finds[1].agency, B);
        assertEq(disagree[2].length, 1);
        assertEq(uint8(statuses[2]), uint8(Lantern.Status.Disputed));
        assertEq(uint8(statuses[0]), uint8(Lantern.Status.Reported));

        (finds,,,) = lantern.getFinds(2, 5);
        assertEq(finds.length, 1);
        assertEq(finds[0].sigmaCm, SIGMA_20M);

        (finds,,,) = lantern.getFinds(3, 5);
        assertEq(finds.length, 0);

        Lantern.Check[] memory checks = lantern.getChecks(0, 10);
        assertEq(checks.length, 1);
        assertEq(checks[0].findId, 2);
    }

    // ----------------------------------------------------------- safety zones

    function test_safetyZone_postAndRead() public {
        uint64 until = uint64(block.timestamp + 3600);
        vm.prank(A);
        vm.expectEmit(true, true, false, true);
        emit Lantern.SafetyZonePosted(0, A, X, Y, 500, until, "drilling");
        uint256 id = lantern.postSafetyZone(X, Y, 500, until, "drilling");
        assertEq(id, 0);
        assertEq(lantern.zoneCount(), 1);
        Lantern.SafetyZone memory z = lantern.getZone(0);
        assertEq(z.agency, A);
        assertEq(z.radiusCm, 500);
        assertEq(z.until, until);
        assertEq(z.purpose, "drilling");
        Lantern.SafetyZone[] memory zones = lantern.getZones(0, 10);
        assertEq(zones.length, 1);
    }

    function test_safetyZone_strangerRefused() public {
        vm.prank(stranger);
        vm.expectRevert(abi.encodeWithSelector(Lantern.NotAnAgency.selector, stranger));
        lantern.postSafetyZone(X, Y, 500, uint64(block.timestamp + 1), "drilling");
    }

    function test_safetyZone_mustEndInTheFuture() public {
        uint64 until = uint64(block.timestamp);
        vm.prank(A);
        vm.expectRevert(abi.encodeWithSelector(Lantern.ZoneAlreadyEnded.selector, until, block.timestamp));
        lantern.postSafetyZone(X, Y, 500, until, "drilling");
    }
}
