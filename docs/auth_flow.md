# Authentication Flow

OAuth 2.0 Authorization Code Flow + OpenID Connect (RFC 6749 · OpenID Connect Core 1.0 · RFC 8252)

```mermaid
sequenceDiagram
    participant U as User
    participant App as Desktop App<br/>─────────────<br/>authlib · keyring
    participant Srv as DepUI<br/>─────────────<br/>Django · authlib
    participant ORCID as orcid.org<br/>─────────────<br/>OIDC Provider

    rect rgb(185, 185, 185)
        Note over App,ORCID: OAuth 2.0 Authorization Code Flow + OIDC (RFC 6749 · OpenID Connect Core 1.0)
    end

    U->>+App: Click "Authenticate"
    App->>App: Generate state (nonce, CSRF token)
    App->>-Browser: os.open( http://<depui>/deposition/auth/oauth/login?state=STATE )

    rect rgb(227, 245, 255)
        Note over U,Browser: OAuth 2.0 Authorization Code Flow with OIDC
        Browser->>+Srv: GET /auth/oauth/login
        Srv->>Srv: Store state in session [CSRF protection]

        Srv-->>-Browser: HTTP 302 →<br/>orcid.org/signin<br/>?response_type=code [Authorization Code Flow]<br/>&client_id=CLIENT_ID<br/>&redirect_uri=REDIR_URL<br/>&scope=openid [OIDC]<br/>&state=STATE<br/>&prompt=login

        Browser->>+ORCID: GET /signin?response_type=code&…
        U->>ORCID: Enter credentials & approve scopes
        ORCID-->>-Browser: HTTP 302 →<br/><depui>/deposition/auth/oauth/callback<br/>?code=AUTH_CODE&state=STATE

        Browser->>+Srv: GET /auth/oauth/callback?code=AUTH_CODE&state=STATE

        Srv->>Srv: Validate state (CSRF check)
        Srv->>+ORCID: POST /oauth/token<br/>grant_type=authorization_code<br/>code=AUTH_CODE · client_id · client_secret · redirect_uri
        ORCID-->>-Srv: access_token + id_token (JWT)<br/>+ token_type + expires_in
        
        Note over Srv: OIDC id_token validation (RS256 / JWKS)
        Srv->>Srv: Fetch ORCID JWKS → verify id_token signature<br/>Validate iss · aud · exp · nonce claims<br/>[PyJWT / authlib]
    end

    rect rgb(185, 185, 185)
        Note over Srv: Issue own tokens
        Srv->>Srv: Mint short-lived API access token (JWT · RS256)<br/>+ opaque refresh token → store in DB
    end

    Srv-->>-Browser: HTTP 302 →<br/>localhost:PORT/callback?code=ONE_TIME_CODE
    Browser->>App: One-time code delivered via loopback [RFC 8252]
    App->>+Srv: POST /auth/token { code: ONE_TIME_CODE }
    Srv->>Srv: Validate & invalidate one-time code
    Srv-->>-App: { access_token, refresh_token } (JSON over TLS)

    App->>App: Persist tokens in OS credential manager<br/>keyring → Keychain (macOS) · DPAPI (Windows) · libsecret (Linux)
```

## Simplified view

```mermaid
sequenceDiagram
    participant U as User
    participant App as Desktop App<br/>─────────────<br/>authlib · keyring
    participant Srv as DepUI<br/>─────────────<br/>Django · authlib
    participant ORCID as orcid.org<br/>─────────────<br/>OIDC Provider

    rect rgb(185, 185, 185)
        Note over App,ORCID: OAuth 2.0 Authorization Code Flow + OIDC (RFC 6749 · OpenID Connect Core 1.0)
    end

    U->>+App: Click "Authenticate"
    App->>App: Generate state (nonce, CSRF token)
    App->>-Browser: os.open( http://<depui>/deposition/auth/oauth/login?state=STATE )

    rect rgb(227, 245, 255)
        Note over Srv,Browser: OneDep - ORCiD OAuth2 Authorization Endpoint
        Browser->>+Srv: Login request
        Srv-->>Browser: Identity verified (id_token)
    end

    rect rgb(185, 185, 185)
        Note over Srv: Issue own tokens
        Srv->>Srv: Mint short-lived API access token (JWT · RS256)<br/>+ opaque refresh token → store in DB
    end

    Srv-->>-Browser: HTTP 302 →<br/>localhost:PORT/callback?code=ONE_TIME_CODE
    Browser->>App: One-time code delivered via loopback [RFC 8252]
    App->>+Srv: POST /auth/token { code: ONE_TIME_CODE }
    Srv->>Srv: Validate & invalidate one-time code
    Srv-->>-App: { access_token, refresh_token } (JSON over TLS)

    App->>App: Persist tokens in OS credential manager<br/>keyring → Keychain (macOS) · DPAPI (Windows) · libsecret (Linux)
```

## Key decisions

| Aspect | Choice | Why |
|---|---|---|
| OAuth2 flow | Authorization Code | User-interactive; code never exposed to client |
| ORCID client type | Confidential (`client_secret`) | ORCID does not support PKCE; secret kept server-side |
| OIDC scope | `openid` | Gets signed `id_token` (JWT) instead of opaque ORCID token |
| id_token sig | RS256 + JWKS | Asymmetric — server verifies without shared secret |
| Our tokens | JWT (access) + opaque (refresh) | Short-lived JWT for stateless API auth; opaque refresh for revocation control |
| Token delivery | One-time code → POST exchange | Keeps tokens out of URLs (no browser history / log leakage) |
| Loopback redirect | `localhost:PORT` | RFC 8252 recommended pattern for native app token delivery |
| Credential storage | `keyring` | Cross-platform OS-native secure storage, no plaintext files |
