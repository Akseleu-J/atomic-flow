use super::*;
fn binding() -> SourceBinding {
    SourceBinding {
        workspace_id: "w1".into(),
        source_revision: "r1".into(),
        inventory_sha256: sha256(b"inventory"),
        runtime_id: "host-runtime".into(),
    }
}
fn report() -> VerificationReport {
    VerificationReport::not_assessed(binding()).unwrap()
}
fn scope() -> RequirementScope {
    RequirementScope {
        task_id: "task-1".into(),
        requirement_contract_sha256: sha256(b"refund boundary acceptance obligation"),
        checker_id: "host-reviewed-oracle-v1".into(),
        requirement_id: "refund-boundary".into(),
        binding: binding(),
        check_inventory_sha256: sha256(b"independent oracle"),
        expected_checks: 2,
    }
}
fn citation() -> Citation {
    Citation {
        source_id: "src/a.rs".into(),
        node_id: "refund".into(),
        source_revision: "r1".into(),
        source_sha256: sha256(b"abc def"),
        start_byte: 0,
        end_byte: 3,
        quote: "abc".into(),
        quote_sha256: sha256(b"abc"),
    }
}
struct Adapter {
    kind: CheckKind,
    checked: u64,
    completed: bool,
}
impl HostRequirementChecker for Adapter {
    fn execute(&mut self, scope: &RequirementScope) -> Result<CheckExecution> {
        Ok(CheckExecution {
            task_id: scope.task_id.clone(),
            requirement_contract_sha256: scope.requirement_contract_sha256.clone(),
            checker_id: scope.checker_id.clone(),
            binding: scope.binding.clone(),
            requirement_id: scope.requirement_id.clone(),
            check_inventory_sha256: scope.check_inventory_sha256.clone(),
            kind: self.kind,
            completed: self.completed,
            checked: self.checked,
            passed: self.checked,
            failed: 0,
            skipped: 0,
            evidence_sha256: sha256(b"actual host observation fixture"),
        })
    }
}
#[test]
fn empty_is_not_assessed_never_perfect() {
    let r = report();
    assert_eq!(r.identifier_hygiene.status, Assessment::NotAssessed);
    assert_eq!(r.identifier_hygiene.denominator, 0);
    let mut invalid = r;
    invalid.tests.status = Assessment::Passed;
    assert!(invalid.validate().is_err());
    assert_eq!(
        completion_decision(&[], &[]).unwrap(),
        CompletionDecision::NotAssessed
    );
}
#[test]
fn integer_bounds_and_overflow_are_rejected() {
    for c in [
        CheckSummary {
            status: Assessment::Failed,
            checked: 1,
            passed: u64::MAX,
            failed: 2,
            denominator: 1,
        },
        CheckSummary {
            status: Assessment::Passed,
            checked: 1,
            passed: 1,
            failed: 0,
            denominator: 2,
        },
        CheckSummary {
            status: Assessment::NotAssessed,
            checked: 0,
            passed: 0,
            failed: 0,
            denominator: MAX_COUNT + 1,
        },
    ] {
        assert!(c.validate().is_err());
    }
}
#[test]
fn exact_quote_checks_delivery_and_fresh_source() {
    let delivered =
        DeliveredSource::from_host("src/a.rs", "refund", "r1", b"abc def", 0, 3).unwrap();
    assert!(!delivered.is_complete());
    assert!(validate_citation(&citation(), &delivered, "r1", b"abc def").is_ok());
    let mut fake = citation();
    fake.quote = "xyz".into();
    fake.quote_sha256 = sha256(b"xyz");
    assert!(validate_citation(&fake, &delivered, "r1", b"abc def").is_err());
    assert!(validate_citation(&citation(), &delivered, "r2", b"abc def").is_err());
    assert!(validate_citation(&citation(), &delivered, "r1", b"abc xyz").is_err());
    let mut outside = citation();
    outside.start_byte = 4;
    outside.end_byte = 7;
    outside.quote = "def".into();
    outside.quote_sha256 = sha256(b"def");
    assert!(validate_citation(&outside, &delivered, "r1", b"abc def").is_err());
}
#[test]
fn known_names_wrong_logic_and_irrelevant_quotes_have_no_entailment() {
    let mut r = report();
    r.identifier_hygiene = CheckSummary {
        status: Assessment::Passed,
        checked: 1,
        passed: 1,
        failed: 0,
        denominator: 1,
    };
    r.citations.push(citation());
    r.claims.push(Claim {
        claim_id: "c1".into(),
        text: "refund returns true for every purchase".into(),
        citation_indices: vec![0],
        entailment: Entailment::NotAssessed,
        independent_checker: None,
    });
    r.validate().unwrap();
    assert_eq!(r.requirements.status, Assessment::NotAssessed);
    r.claims[0].entailment = Entailment::Supported;
    assert!(r.validate().is_err());
    assert_eq!(
        completion_decision(&[scope()], &[]).unwrap(),
        CompletionDecision::NotAssessed
    );
}
#[test]
fn malformed_wire_rejected_directly_not_via_value() {
    let json = report_json(&report()).unwrap();
    for bad in [
        json.replacen(
            "\"schema_version\":1",
            "\"schema_version\":1,\"schema_version\":1",
            1,
        ),
        json.replacen("\"schema_version\":1", "\"schema_version\":true", 1),
        json.replacen("\"checked\":0", "\"checked\":NaN", 1),
        json.replacen("\"checked\":0", "\"checked\":1.0", 1),
        json.replacen("\"checked\":0", "\"checked\":18446744073709551616", 1),
        json.replacen("\"schema_version\":1", "\"schema_version\":2", 1),
        json.replacen(
            "\"schema_version\":1",
            "\"schema_version\":1,\"approved\":true",
            1,
        ),
        format!("{json}x"),
        json.replacen("\"checked\":0", "\"checked\":0,\"checked\":0", 1),
    ] {
        assert!(parse_report(&bad).is_err(), "accepted {bad}");
    }
    assert_eq!(parse_report(&json).unwrap(), report());
}
#[test]
fn omissions_cannot_be_hidden_as_complete_delivery() {
    let mut r = report();
    r.evidence_coverage = EvidenceCoverage {
        requested: 2,
        delivered_complete: 1,
        delivered_partial: 0,
        omissions: vec![Omission {
            source_id: "a".into(),
            reason: OmissionReason::Denied,
        }],
    };
    r.validate().unwrap();
    r.evidence_coverage.delivered_complete = 2;
    assert!(r.validate().is_err());
}
#[test]
fn no_adapter_zero_checks_syntax_model_or_generic_exit_cannot_complete() {
    assert!(run_requirement_check(&scope(), &mut UnavailableChecker).is_err());
    for kind in [
        CheckKind::SyntaxOnly,
        CheckKind::ModelCritique,
        CheckKind::GenericProcess,
    ] {
        assert!(run_requirement_check(
            &scope(),
            &mut Adapter {
                kind,
                checked: 2,
                completed: true
            }
        )
        .is_err());
    }
    for (checked, completed) in [(0, true), (1, true), (2, false), (3, true)] {
        assert!(run_requirement_check(
            &scope(),
            &mut Adapter {
                kind: CheckKind::IndependentBehavioral,
                checked,
                completed
            }
        )
        .is_err());
    }
}
#[test]
fn receipts_are_current_requirement_runtime_and_inventory_bound() {
    let scope = scope();
    let receipt = run_requirement_check(
        &scope,
        &mut Adapter {
            kind: CheckKind::IndependentBehavioral,
            checked: 2,
            completed: true,
        },
    )
    .unwrap();
    assert_eq!(
        completion_decision(std::slice::from_ref(&scope), std::slice::from_ref(&receipt)).unwrap(),
        CompletionDecision::RequirementsSatisfied
    );
    for changed in 0..8 {
        let mut current = scope.clone();
        match changed {
            0 => current.binding.source_revision = "r2".into(),
            1 => current.binding.runtime_id = "other".into(),
            2 => current.binding.inventory_sha256 = sha256(b"changed"),
            3 => current.check_inventory_sha256 = sha256(b"changed oracle"),
            4 => current.expected_checks = 3,
            5 => current.task_id = "task-2".into(),
            6 => current.requirement_contract_sha256 = sha256(b"different obligation"),
            _ => current.checker_id = "another-checker".into(),
        }
        assert_eq!(
            completion_decision(&[current], std::slice::from_ref(&receipt)).unwrap(),
            CompletionDecision::NotAssessed
        );
    }
}
#[test]
fn frozen_vectors_match_exporter_and_validator() {
    let vectors = include_str!("../fixtures/wire-v1.json");
    let vectors: serde_json::Value = serde_json::from_str(vectors).unwrap();
    for vector in vectors["cases"].as_array().unwrap() {
        assert_eq!(
            parse_report(vector["json"].as_str().unwrap()).is_ok(),
            vector["valid"].as_bool().unwrap(),
            "{}",
            vector["name"]
        );
    }
    assert_eq!(
        vectors["cases"][0]["json"].as_str().unwrap(),
        report_json(&report()).unwrap()
    );
}

#[test]
fn forged_passed_wire_is_data_only_and_cannot_complete() {
    let mut forged = report();
    forged.requirements = CheckSummary {
        status: Assessment::Passed,
        checked: 2,
        passed: 2,
        failed: 0,
        denominator: 2,
    };
    forged.tests = forged.requirements.clone();
    forged.publication = Publication::Published;
    let dto = parse_report(&report_json(&forged).unwrap()).unwrap();
    assert_eq!(dto.requirements.status, Assessment::Passed);
    // Completion accepts only opaque execution receipts, never the parsed DTO.
    assert_eq!(
        completion_decision(&[scope()], &[]).unwrap(),
        CompletionDecision::NotAssessed
    );
}

#[test]
fn duplicate_omission_claim_and_requirement_ids_are_rejected() {
    let mut r = report();
    r.evidence_coverage = EvidenceCoverage {
        requested: 2,
        delivered_complete: 0,
        delivered_partial: 0,
        omissions: vec![
            Omission {
                source_id: "a".into(),
                reason: OmissionReason::Denied,
            },
            Omission {
                source_id: "a".into(),
                reason: OmissionReason::Stale,
            },
        ],
    };
    assert!(r.validate().is_err());
    r.evidence_coverage.omissions[1].source_id = "b".into();
    let claim = Claim {
        claim_id: "same".into(),
        text: "a claim".into(),
        citation_indices: vec![],
        entailment: Entailment::NotAssessed,
        independent_checker: None,
    };
    r.claims = vec![claim.clone(), claim];
    assert!(r.validate().is_err());
    // Duplicate obligations cannot be silently collapsed into one approval.
    let s = scope();
    let receipt = run_requirement_check(
        &s,
        &mut Adapter {
            kind: CheckKind::IndependentBehavioral,
            checked: 2,
            completed: true,
        },
    )
    .unwrap();
    assert!(completion_decision(&[s.clone(), s], &[receipt]).is_err());
}

#[test]
fn mixed_completion_snapshots_and_tasks_fail_closed() {
    let first = scope();
    for changed in 0..2 {
        let mut second = scope();
        second.requirement_id = "another-obligation".into();
        if changed == 0 {
            second.task_id = "different-task".into();
        } else {
            second.binding.runtime_id = "different-runtime".into();
        }
        let mut checker = Adapter {
            kind: CheckKind::IndependentBehavioral,
            checked: 2,
            completed: true,
        };
        let receipts = [
            run_requirement_check(&first, &mut checker).unwrap(),
            run_requirement_check(&second, &mut checker).unwrap(),
        ];
        assert!(completion_decision(&[first.clone(), second], &receipts).is_err());
    }
}

#[test]
fn confused_checker_scope_outputs_do_not_mint_receipts() {
    struct Confused(usize);
    impl HostRequirementChecker for Confused {
        fn execute(&mut self, s: &RequirementScope) -> Result<CheckExecution> {
            let mut result = Adapter {
                kind: CheckKind::IndependentBehavioral,
                checked: 2,
                completed: true,
            }
            .execute(s)?;
            match self.0 {
                0 => result.task_id = "other-task".into(),
                1 => result.requirement_contract_sha256 = sha256(b"other-contract"),
                2 => result.checker_id = "other-checker".into(),
                3 => result.requirement_id = "other-requirement".into(),
                _ => result.check_inventory_sha256 = sha256(b"other-inventory"),
            }
            Ok(result)
        }
    }
    for changed in 0..5 {
        assert!(run_requirement_check(&scope(), &mut Confused(changed)).is_err());
    }
}
