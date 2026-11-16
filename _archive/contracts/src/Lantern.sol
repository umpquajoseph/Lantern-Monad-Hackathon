// SPDX-License-Identifier: Apache-2.0
pragma solidity ^0.8.28;

/// @title Lantern: a shared record of lunar ice finds that rovers write themselves.
/// @notice Two or more agencies, each one signing address fixed at deployment, post ice
/// finds together with the position uncertainty of the rover that made them, and check
/// each other's finds. The contract enforces the checking rules of docs/spec.html:
///   - only an agency may write, and only a *different* agency may check a find;
///   - the checker must have measured within CHECK_RANGE_MULTIPLIER times the two
///     position uncertainties added together;
///   - a find is Disputed if any agency disagrees, Confirmed if at least one agrees and
///     none disagree, otherwise Reported.
/// Nothing is edited or deleted: disagreements stay on the record, marked.
///
/// Units (docs/handoffs.md, step 1): positions and sigmas are whole centimetres in the
/// world frame (x east, y north; south polar stereographic metres on a LOLA tile).
/// `reading` is the ice sensor reading in hundredths of wt% water-equivalent (ASSUMED
/// unit: 560 = 5.60 wt%, the LCROSS Cabeus value cited in the PROSPECT team brief).
/// `dataHash` is keccak256 of the canonical raw-readings JSON, so anyone holding the raw
/// file can re-hash it and compare.
///
/// Artemis Accords framing: signatories commit to share scientific data openly (section 8)
/// and to notify others of the safety zones around their operations (section 11). The
/// Accords do not require reports of prospecting finds. This contract is a public,
/// tamper-evident way to meet those two commitments; it is not itself a legal requirement.
contract Lantern {
    enum Status {
        Reported,
        Confirmed,
        Disputed
    }

    struct Find {
        address agency; // the finder's signing address
        int64 xCm; // world frame, centimetres
        int64 yCm;
        uint64 sigmaCm; // 1-sigma radial position uncertainty, centimetres
        uint32 reading; // hundredths of wt% water-equivalent (see contract notice)
        bytes32 dataHash; // keccak256 of the raw readings JSON
        uint64 time; // block timestamp when posted
    }

    struct Check {
        uint256 findId;
        address agency; // the checker's signing address
        int64 xCm; // where the checker measured, centimetres
        int64 yCm;
        uint64 sigmaCm;
        uint32 reading;
        bool agrees;
        uint64 time;
    }

    /// @notice A safety zone (Artemis Accords, section 11): an agency announces the area it
    /// is working in, for how long, and why, so other rovers can route around it. Zones are
    /// temporary by design: `until` is when the zone ends.
    struct SafetyZone {
        address agency;
        int64 xCm;
        int64 yCm;
        uint64 radiusCm;
        uint64 until; // unix time when the zone ends
        string purpose;
        uint64 time;
    }

    /// @dev docs/spec.html, contract functions: a check is refused "if it measured farther from
    /// the find than three times the two uncertainties added together".
    uint256 public constant CHECK_RANGE_MULTIPLIER = 3;

    address[] private _agencies;
    mapping(address => bool) public isAgency;
    mapping(address => string) public agencyName;

    Find[] private _finds;
    Check[] private _checks;
    SafetyZone[] private _zones;
    mapping(uint256 => address[]) private _agree;
    mapping(uint256 => address[]) private _disagree;
    mapping(uint256 => uint256[]) private _checksOf;
    mapping(uint256 => mapping(address => bool)) public hasChecked;

    event FindPosted(
        uint256 indexed id,
        address indexed agency,
        int64 xCm,
        int64 yCm,
        uint64 sigmaCm,
        uint32 reading,
        bytes32 dataHash
    );
    event FindChecked(
        uint256 indexed id,
        uint256 indexed checkId,
        address indexed agency,
        bool agrees,
        int64 xCm,
        int64 yCm,
        uint64 sigmaCm,
        uint32 reading,
        Status status
    );
    event SafetyZonePosted(
        uint256 indexed id,
        address indexed agency,
        int64 xCm,
        int64 yCm,
        uint64 radiusCm,
        uint64 until,
        string purpose
    );

    error NotAnAgency(address caller);
    error NoSuchFind(uint256 id);
    error NoSuchCheck(uint256 id);
    error NoSuchZone(uint256 id);
    error OwnFind(uint256 id, address agency);
    error AlreadyChecked(uint256 id, address agency);
    error TooFar(uint256 id, uint256 distanceSqCm2, uint256 allowedSqCm2);
    error BadAgencies();
    error ZoneAlreadyEnded(uint64 until, uint256 now_);

    /// @param agencies_ One signing address per agency, fixed for the life of the contract.
    /// @param names_ A display name per agency, same order.
    constructor(address[] memory agencies_, string[] memory names_) {
        if (agencies_.length < 2 || agencies_.length != names_.length) revert BadAgencies();
        for (uint256 i = 0; i < agencies_.length; i++) {
            address a = agencies_[i];
            if (a == address(0) || isAgency[a]) revert BadAgencies();
            isAgency[a] = true;
            agencyName[a] = names_[i];
            _agencies.push(a);
        }
    }

    modifier onlyAgency() {
        if (!isAgency[msg.sender]) revert NotAnAgency(msg.sender);
        _;
    }

    // ------------------------------------------------------------------ writes

    /// @notice Post an ice find. Status starts as Reported.
    /// @return id The find's ID, its index in the record.
    function postFind(int64 xCm, int64 yCm, uint64 sigmaCm, uint32 reading, bytes32 dataHash)
        external
        onlyAgency
        returns (uint256 id)
    {
        id = _finds.length;
        _finds.push(
            Find({
                agency: msg.sender,
                xCm: xCm,
                yCm: yCm,
                sigmaCm: sigmaCm,
                reading: reading,
                dataHash: dataHash,
                time: uint64(block.timestamp)
            })
        );
        emit FindPosted(id, msg.sender, xCm, yCm, sigmaCm, reading, dataHash);
    }

    /// @notice Check another agency's find from where you measured. Refused if you are the
    /// finder, if you already checked this find, or if you measured too far from it.
    /// Agree or disagree, the check is kept and the find's status is recomputed.
    /// @return checkId The check's ID, its index in the check record.
    function checkFind(
        uint256 id,
        int64 xCm,
        int64 yCm,
        uint64 sigmaCm,
        uint32 reading,
        bool agrees
    ) external onlyAgency returns (uint256 checkId) {
        if (id >= _finds.length) revert NoSuchFind(id);
        Find storage f = _finds[id];
        if (f.agency == msg.sender) revert OwnFind(id, msg.sender);
        if (hasChecked[id][msg.sender]) revert AlreadyChecked(id, msg.sender);
        {
            // Scoped so these leave the stack before the emit below (stack too deep otherwise).
            (bool ok, uint256 d2, uint256 a2) =
                withinCheckRange(f.xCm, f.yCm, f.sigmaCm, xCm, yCm, sigmaCm);
            if (!ok) revert TooFar(id, d2, a2);
        }

        hasChecked[id][msg.sender] = true;
        checkId = _checks.length;
        _checks.push(
            Check({
                findId: id,
                agency: msg.sender,
                xCm: xCm,
                yCm: yCm,
                sigmaCm: sigmaCm,
                reading: reading,
                agrees: agrees,
                time: uint64(block.timestamp)
            })
        );
        _checksOf[id].push(checkId);
        if (agrees) _agree[id].push(msg.sender);
        else _disagree[id].push(msg.sender);
        Status status = statusOf(id);
        emit FindChecked(id, checkId, msg.sender, agrees, xCm, yCm, sigmaCm, reading, status);
    }

    /// @notice Announce a temporary safety zone (Artemis Accords, section 11).
    function postSafetyZone(
        int64 xCm,
        int64 yCm,
        uint64 radiusCm,
        uint64 until,
        string calldata purpose
    ) external onlyAgency returns (uint256 id) {
        if (until <= block.timestamp) revert ZoneAlreadyEnded(until, block.timestamp);
        id = _zones.length;
        _zones.push(
            SafetyZone({
                agency: msg.sender,
                xCm: xCm,
                yCm: yCm,
                radiusCm: radiusCm,
                until: until,
                purpose: purpose,
                time: uint64(block.timestamp)
            })
        );
        emit SafetyZonePosted(id, msg.sender, xCm, yCm, radiusCm, until, purpose);
    }

    // ------------------------------------------------------------------- reads

    /// @notice The distance rule, exposed so clients and tests can evaluate it off chain.
    /// @return ok True when the checker is close enough to the find.
    /// @return distanceSq Squared distance between the two positions, cm².
    /// @return allowedSq Squared allowed distance, (3 × (σ_find + σ_check))², cm².
    function withinCheckRange(
        int64 findX,
        int64 findY,
        uint64 findSigma,
        int64 checkX,
        int64 checkY,
        uint64 checkSigma
    ) public pure returns (bool ok, uint256 distanceSq, uint256 allowedSq) {
        // int64 differences fit in int256 with room to spare: |dx| < 2^65, dx² < 2^130.
        int256 dx = int256(checkX) - int256(findX);
        int256 dy = int256(checkY) - int256(findY);
        distanceSq = uint256(dx * dx + dy * dy);
        uint256 allowed = CHECK_RANGE_MULTIPLIER * (uint256(findSigma) + uint256(checkSigma));
        allowedSq = allowed * allowed;
        ok = distanceSq <= allowedSq;
    }

    /// @notice Disputed if any agency disagrees; Confirmed if at least one agrees and none
    /// disagree; otherwise Reported.
    function statusOf(uint256 id) public view returns (Status) {
        if (id >= _finds.length) revert NoSuchFind(id);
        if (_disagree[id].length > 0) return Status.Disputed;
        if (_agree[id].length > 0) return Status.Confirmed;
        return Status.Reported;
    }

    function findCount() external view returns (uint256) {
        return _finds.length;
    }

    function checkCount() external view returns (uint256) {
        return _checks.length;
    }

    function zoneCount() external view returns (uint256) {
        return _zones.length;
    }

    function agencyCount() external view returns (uint256) {
        return _agencies.length;
    }

    /// @notice Every agency, in deployment order, with its display name.
    function agencies() external view returns (address[] memory addrs, string[] memory names) {
        addrs = _agencies;
        names = new string[](addrs.length);
        for (uint256 i = 0; i < addrs.length; i++) {
            names[i] = agencyName[addrs[i]];
        }
    }

    /// @notice Everything about one find: the record, who agrees, who disagrees, status.
    function getFind(uint256 id)
        external
        view
        returns (Find memory find, address[] memory agree, address[] memory disagree, Status status)
    {
        if (id >= _finds.length) revert NoSuchFind(id);
        return (_finds[id], _agree[id], _disagree[id], statusOf(id));
    }

    /// @notice A page of finds in one call, for dashboards. `count` is clamped to what
    /// exists; a `start` past the end returns empty arrays.
    function getFinds(uint256 start, uint256 count)
        external
        view
        returns (
            Find[] memory finds,
            address[][] memory agree,
            address[][] memory disagree,
            Status[] memory statuses
        )
    {
        uint256 n = _finds.length;
        if (start >= n) count = 0;
        else if (count > n - start) count = n - start;
        finds = new Find[](count);
        agree = new address[][](count);
        disagree = new address[][](count);
        statuses = new Status[](count);
        for (uint256 i = 0; i < count; i++) {
            uint256 id = start + i;
            finds[i] = _finds[id];
            agree[i] = _agree[id];
            disagree[i] = _disagree[id];
            statuses[i] = statusOf(id);
        }
    }

    function getCheck(uint256 checkId) external view returns (Check memory) {
        if (checkId >= _checks.length) revert NoSuchCheck(checkId);
        return _checks[checkId];
    }

    /// @notice IDs of every check made on a find, oldest first.
    function checksOf(uint256 id) external view returns (uint256[] memory) {
        if (id >= _finds.length) revert NoSuchFind(id);
        return _checksOf[id];
    }

    function getChecks(uint256 start, uint256 count) external view returns (Check[] memory checks) {
        uint256 n = _checks.length;
        if (start >= n) count = 0;
        else if (count > n - start) count = n - start;
        checks = new Check[](count);
        for (uint256 i = 0; i < count; i++) {
            checks[i] = _checks[start + i];
        }
    }

    function getZone(uint256 id) external view returns (SafetyZone memory) {
        if (id >= _zones.length) revert NoSuchZone(id);
        return _zones[id];
    }

    function getZones(uint256 start, uint256 count)
        external
        view
        returns (SafetyZone[] memory zones)
    {
        uint256 n = _zones.length;
        if (start >= n) count = 0;
        else if (count > n - start) count = n - start;
        zones = new SafetyZone[](count);
        for (uint256 i = 0; i < count; i++) {
            zones[i] = _zones[start + i];
        }
    }
}
