//! Export the frozen cross-language contract corpus; does not execute checks.
fn main() {
    print!("{}", include_str!("../fixtures/wire-v1.json"));
}
