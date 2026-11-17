# Two-minute demo: Mission Control (`/mission.html`)

1. **0:00 Start.** "Two rovers, two jobs. The satellite only told them where to
   look: the amber rings." Point at *Who knows what*.
2. **0:15 Surveyor drives.** Third person. Name tag says *neutron survey*.
   Switch to **First person**: the route it is following is drawn on its own
   camera frame.
3. **0:30 First site.** The clock slows by itself and the camera zooms in: arm
   out, drill down, dust in ballistic arcs. *Data being recorded* fills in
   every 10 cm. Result: water ice. The site turns **red**.
4. **0:50 On chain, in plain words.** Read the card: what went on chain (a
   site id, a position, a result, two hashes), what stayed off chain. Press
   **Verify it**: the browser re-hashes the report and it matches.
5. **1:10 A negative.** The next site drills dry and is published too.
   "Failures are on the record."
6. **1:25 Prospector.** Switch rover. It read the registry, picked the nearest
   positive site, fetched the Surveyor's route by its hash. **Overhead**: the
   new obstacle, and its route bending around it.
7. **1:45 Linked reports.** Click a red site: candidate -> inspection ->
   analysis, each naming the one before. Nothing was edited.
8. **1:55 Honest close.** The badge says LOCAL CHAIN: real contract, not Monad
   yet; deposits and readings are simulated. The chain proves who published
   what and that it has not changed, not that the ice is there: that is what
   the second rover is for.
