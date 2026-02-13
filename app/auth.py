# =============================================================================
# Fensterkraftwerk – Einfache Token-Authentifizierung
# =============================================================================
#
# Schützt alle API-Endpunkte mit einem einzigen gemeinsamen Token.
# Das Token wird als Umgebungsvariable API_TOKEN konfiguriert und
# dient gleichzeitig als „Passwort" für die Web-Oberfläche.
#
# Unterstützte Methoden:
#   1. Authorization: Bearer <token>  (Header)
#   2. ?token=<token>                 (Query-Parameter, für QR-Codes)
#
# =============================================================================

import logging
from typing import Optional

from fastapi import Depends, HTTPException, Query, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import settings

logger = logging.getLogger("fensterkraftwerk.auth")

# Optional Bearer-Token (nicht required, damit Query-Param auch geht)
_bearer_scheme = HTTPBearer(auto_error=False)


async def verify_token(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer_scheme),
    token: Optional[str] = Query(default=None, alias="token", include_in_schema=False),
) -> str:
    """
    FastAPI Dependency – prüft, ob ein gültiges Token vorliegt.

    Akzeptiert das Token entweder als:
      - Bearer-Token im Authorization-Header
      - Query-Parameter ?token=xxx (praktisch für QR-Code-Links)

    Raises:
        HTTPException 401 bei fehlendem oder ungültigem Token.

    Returns:
        Das validierte Token als String.
    """
    # Token aus Header oder Query extrahieren
    provided_token = None

    if credentials and credentials.credentials:
        provided_token = credentials.credentials
    elif token:
        provided_token = token

    if not provided_token:
        raise HTTPException(
            status_code=401,
            detail="Authentifizierung erforderlich. Bitte Token angeben.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if provided_token != settings.api_token:
        logger.warning(f"Ungültiger Login-Versuch von {request.client.host}")
        raise HTTPException(
            status_code=401,
            detail="Ungültiges Token / Passwort.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return provided_token
