//! Integer budget admission. An unknown request never authorizes a reserve.
pub fn can_reserve(spent: u64, held: u64, cap: u64, requested: Option<u64>) -> bool {
    let total = spent.checked_add(held).and_then(|used| {
        requested.and_then(|amount| used.checked_add(amount))
    });
    match total {
        Some(total) => total < cap,
        None => false,
    }
}
