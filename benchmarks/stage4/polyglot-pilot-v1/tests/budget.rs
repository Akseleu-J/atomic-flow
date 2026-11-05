use atomic_budget_pilot::can_reserve;

#[test]
fn exact_cap_is_admitted() {
    assert!(can_reserve(6, 2, 10, Some(2)));
}

#[test]
fn under_cap_is_admitted() {
    assert!(can_reserve(6, 2, 10, Some(1)));
}

#[test]
fn unknown_request_is_refused() {
    assert!(!can_reserve(6, 2, 10, None));
}

#[test]
fn integer_overflow_is_refused() {
    assert!(!can_reserve(u64::MAX, 1, u64::MAX, Some(0)));
}
