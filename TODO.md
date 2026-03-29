# Media Bot Project Status

## Accomplished
- [x] Initial CLI setup (`main.py`)
- [x] Radarr API Wrapper (`api/radarr.py`)
  - Status retrieval
  - Movie lookup and addition
- [x] Sonarr API Wrapper (`api/sonarr.py`)
  - Status retrieval
  - Series lookup and addition
- [x] Installed and started NVIDIA NeMo Claw via Docker (`openshell-cluster-nemoclaw` container)

## In Progress
- [ ] Connect Python Media bot to the NeMo Claw API.
- [ ] Implement Tool schemas for Radarr & Sonarr actions.
- [ ] Establish LLM chat loop for processing natural language requests.

## Future / Pending
- [ ] Lidarr API interactions (add artist).
- [ ] Add listing/deletion commands.
- [ ] Refactoring architecture for long-term support.
- [ ] Web UI / External Access setup via reverse proxy / Google Cloud.
- [ ] Tautulli status reports and WhatsApp alert integrations.
