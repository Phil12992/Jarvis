"""Fehler des Agenten, unabhaengig vom Provider.

Die Provider-Ausnahmen (RateLimit, Timeout, 5xx) sind Sache von
jarvis.llm.router und gehoeren dorthin. Der Agent-Loop muss aber wissen,
dass er die komplette Kette durch ist - und das ist eine Aussage ueber
*ihn*, nicht ueber OpenRouter.

Deshalb steht LLMUnavailable hier und nicht in router.py. Sonst waere der
gesamte Agent-Loop nur testbar, wenn litellm installiert ist, also nur auf
der Maschine, auf der auch inference laeuft.
"""


class JarvisError(Exception):
    """Basisklasse fuer alles, was der Agent als eigenes Problem meldet."""


class LLMUnavailable(JarvisError):
    """Alle Modelle in der Kette sind gescheitert.

    unterscheidet sich von einem Timeout so, dass ein Timeout ein
    Wiederholen wert ist, ein LLMUnavailable meistens nicht: wenn OpenRouter
    und der Fallback beide streiken, hilft Warten.
    """


class ToolDenied(JarvisError):
    """Werkzeug nicht freigegeben oder nicht bestaetigt."""


class ToolFailed(JarvisError):
    """Werkzeug lief, gab aber einen Fehler zurueck."""
