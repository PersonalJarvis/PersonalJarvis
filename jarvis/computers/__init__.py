"""Computers: the user's own servers and virtual machines, reachable over SSH.

The Settings -> Computers section registers machines Jarvis may work on — a
rented VPS (Hostinger, Hetzner, DigitalOcean or any SSH host) or a local
virtual machine Jarvis creates itself through Multipass. This package is the
foundation later features build on: every machine is one :class:`Computer`
record, reachable through :func:`jarvis.computers.service.get_service`.

Layout:

* :mod:`.models`    — the records (Computer, facts, health).
* :mod:`.store`     — the JSON file the records live in (atomic writes).
* :mod:`.identity`  — Jarvis's own SSH key pair (private half in the keyring).
* :mod:`.probe`     — the one read-only shell script a health check runs.
* :mod:`.ssh`       — connect / run / install-key over ``asyncssh``.
* :mod:`.cloud`     — import servers from a hosting account's API.
* :mod:`.local_vm`  — create and drive local VMs through Multipass.
* :mod:`.service`   — the orchestration the REST routes call.

Secrets never touch the JSON file: the private key, a kept password and the
hosting-account tokens go through ``get_secret`` / ``set_secret`` (AP-12).
"""
