//! MVP dashboard v0.1: single-file, zero-build. Served by `af serve` at
//! GET /dashboard — open http://127.0.0.1:8787/dashboard in a browser while
//! the server runs. Talks only to same-origin af serve endpoints; the API
//! key never reaches the browser (env var names, not values).

pub const DASHBOARD_HTML: &str = include_str!("dashboard.html");
