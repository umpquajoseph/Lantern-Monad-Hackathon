# Lantern

A shared map of lunar ice that rovers write themselves, built at Monad Blitz
Oregon, 15 November 2026. Two simulated rovers from two agencies navigate a dark
crater by matching lamp-lit crater rims to an orbital map, post ice finds with
their position uncertainty to a contract on Monad testnet, and check each
other's finds.

Open `docs/spec.html` in a browser for the goal, the demo and the six build
steps. Data formats between steps are in `docs/handoffs.md`.

## Setup

1. Python 3.11 or newer, then
   `pip install numpy opencv-python matplotlib web3 python-dotenv`.
2. Foundry, from https://getfoundry.sh.
3. A throwaway testnet wallet per agency, funded from the Monad testnet faucet.
4. Copy `.env.example` to `.env` and fill it in. Never commit `.env`.

## Sources

- Cauligi et al., ShadowNav, JPL, 2023: https://arxiv.org/abs/2301.04630
  (local copy and text extract in `docs/sources/`)
- PROSPECT Team Brief, RASC-AL 2027 Theme 3-1:
  `docs/sources/PROSPECT-Team-Brief-RASC-AL-2027-Theme-3-1.pdf`
- LOLA south pole elevation maps, NASA Goddard PGDA:
  https://pgda.gsfc.nasa.gov/products/90
- Monad documentation: https://docs.monad.xyz
