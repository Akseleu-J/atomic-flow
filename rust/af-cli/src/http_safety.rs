//! Request boundaries for the local Studio host. Browser data never grants
//! provider credentials or a wider filesystem root.
use super::ServerState;
use axum::{
    extract::{Request, State},
    http::{HeaderMap, HeaderValue, StatusCode},
    middleware::Next,
    response::{IntoResponse, Response},
};

fn authority(value: &str, port: u16, token_mode: bool) -> bool {
    if value.len() > 255 || value.contains(['@', '/', '\\', '?', '#', ' ', '\t', '\r', '\n']) {
        return false;
    }
    if value.eq_ignore_ascii_case(&format!("localhost:{port}"))
        || (port == 80 && value.eq_ignore_ascii_case("localhost"))
    {
        return true;
    }
    let address = value.parse::<std::net::SocketAddr>().ok().or_else(|| {
        (port == 80)
            .then(|| {
                value
                    .parse::<std::net::IpAddr>()
                    .ok()
                    .map(|ip| std::net::SocketAddr::new(ip, 80))
            })
            .flatten()
    });
    address
        .is_some_and(|address| address.port() == port && (token_mode || address.ip().is_loopback()))
}

pub(super) fn request_allowed(headers: &HeaderMap, port: u16, token_mode: bool) -> bool {
    if headers.get_all("host").iter().count() != 1 || headers.get_all("origin").iter().count() > 1 {
        return false;
    }
    let Some(host) = headers.get("host").and_then(|value| value.to_str().ok()) else {
        return false;
    };
    if !authority(host, port, token_mode) {
        return false;
    }
    if let Some(site) = headers.get("sec-fetch-site") {
        if !matches!(site.to_str(), Ok("same-origin" | "none")) {
            return false;
        }
    }
    if let Some(origin) = headers.get("origin") {
        let Ok(origin) = origin.to_str() else {
            return false;
        };
        // This server is plain HTTP. TLS termination is a distinct host profile.
        let Some(origin_host) = origin.strip_prefix("http://") else {
            return false;
        };
        if !origin_host.eq_ignore_ascii_case(host) {
            return false;
        }
    }
    true
}

pub(super) async fn guard(
    State(state): State<ServerState>,
    request: Request,
    next: Next,
) -> Response {
    let mismatched_authority = request.uri().authority().is_some_and(|uri_authority| {
        request
            .headers()
            .get("host")
            .and_then(|value| value.to_str().ok())
            .is_none_or(|host| !uri_authority.as_str().eq_ignore_ascii_case(host))
    });
    if mismatched_authority
        || !request_allowed(request.headers(), state.port, state.token.is_some())
    {
        return (
            StatusCode::FORBIDDEN,
            "request origin or host is not admitted",
        )
            .into_response();
    }
    let mut response = next.run(request).await;
    let headers = response.headers_mut();
    headers.insert(
        "X-Content-Type-Options",
        HeaderValue::from_static("nosniff"),
    );
    headers.insert("X-Frame-Options", HeaderValue::from_static("DENY"));
    headers.insert("Referrer-Policy", HeaderValue::from_static("no-referrer"));
    headers.insert("Cache-Control", HeaderValue::from_static("no-store"));
    headers.insert(
        "Permissions-Policy",
        HeaderValue::from_static("camera=(), microphone=(), geolocation=()"),
    );
    response
}

#[cfg(test)]
mod tests {
    use super::*;
    fn headers(host: &str, origin: Option<&str>, site: Option<&str>) -> HeaderMap {
        let mut headers = HeaderMap::new();
        headers.insert("host", host.parse().unwrap());
        if let Some(origin) = origin {
            headers.insert("origin", origin.parse().unwrap());
        }
        if let Some(site) = site {
            headers.insert("sec-fetch-site", site.parse().unwrap());
        }
        headers
    }
    #[test]
    fn only_exact_local_authority_and_same_origin_are_admitted() {
        for host in ["localhost:8787", "127.0.0.1:8787", "[::1]:8787"] {
            assert!(request_allowed(
                &headers(host, Some(&format!("http://{host}")), Some("same-origin")),
                8787,
                false
            ));
            assert!(request_allowed(&headers(host, None, None), 8787, false));
        }
        for host in [
            "evil.example:8787",
            "localhost.evil.example:8787",
            "127.0.0.1:8000",
            "192.168.0.2:8787",
            "user@127.0.0.1:8787",
        ] {
            assert!(
                !request_allowed(&headers(host, None, None), 8787, false),
                "{host}"
            );
        }
        assert!(!request_allowed(
            &headers("127.0.0.1:8787", Some("http://evil.example"), None),
            8787,
            false
        ));
        assert!(!request_allowed(
            &headers("127.0.0.1:8787", Some("null"), None),
            8787,
            false
        ));
        assert!(!request_allowed(
            &headers("127.0.0.1:8787", None, Some("cross-site")),
            8787,
            false
        ));
        assert!(!request_allowed(&HeaderMap::new(), 8787, false));
    }
    #[test]
    fn authenticated_remote_ip_still_requires_its_exact_origin_and_port() {
        assert!(request_allowed(
            &headers("192.168.0.2:8787", Some("http://192.168.0.2:8787"), None),
            8787,
            true
        ));
        assert!(!request_allowed(
            &headers("192.168.0.2:8787", Some("http://evil.example"), None),
            8787,
            true
        ));
        assert!(!request_allowed(
            &headers("evil.example:8787", Some("http://evil.example:8787"), None),
            8787,
            true
        ));
    }
}
