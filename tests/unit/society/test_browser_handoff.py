"""Deterministic browser-vs-desktop routing tests."""
from __future__ import annotations

import pytest

from jarvis.society.browser.tool import task_requires_desktop_handoff


@pytest.mark.parametrize(
    "task",
    [
        "Click the address bar and paste this URL",
        "Open the browser extension button",
        "Use the native file picker to upload the report",
        "Resize the browser window and move it to the left monitor",
        "Switch to another app after downloading the file",
        "Klicke in die Adressleiste",
        "Apri la barra degli indirizzi",
        "Usa la finestra di dialogo file",
        "Minimizza la finestra del browser",
        "Cambia app e trascina il file tra le app",
    ],
)
def test_native_browser_or_cross_app_tasks_handoff(task: str) -> None:
    assert task_requires_desktop_handoff(task) is True


@pytest.mark.parametrize(
    "task",
    [
        "Open https://example.com and summarize the page",
        "Click the Sign in link on the webpage",
        "Fill the checkout form but do not submit it",
        "Open a new tab with the documentation",
        "Read my Gmail inbox in the logged-in web app",
        "Apri una nuova scheda e cerca la documentazione",
        "Compila il modulo nella pagina senza inviarlo",
    ],
)
def test_dom_page_tasks_stay_with_browser_use(task: str) -> None:
    assert task_requires_desktop_handoff(task) is False
