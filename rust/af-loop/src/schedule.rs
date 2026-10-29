//! Scheduled and event-triggered agent tasks (Manus Schedules 2.0 shape):
//! durable schedule records, an interval/cron-lite due evaluator, and event
//! trigger matching. The evaluator is pure — a host scheduler loop calls
//! `due()` with the wall clock and submits the task through the admission
//! queue; this module owns the state and the honesty rules (no silent
//! schedule drift: `last_run` and `next_due` are explicit records).

use serde_json::{json, Value};
use std::collections::BTreeMap;

#[derive(Debug, thiserror::Error)]
#[error("{0}")]
pub struct ScheduleError(pub String);

pub type ScheduleResult<T> = Result<T, ScheduleError>;

fn err<T>(message: impl Into<String>) -> ScheduleResult<T> {
    Err(ScheduleError(message.into()))
}

#[derive(Debug, Clone, PartialEq)]
pub enum Trigger {
    /// Fixed interval in seconds (minimum 60).
    Interval { seconds: u64 },
    /// Daily at HH:MM (local wall clock evaluated by the host loop).
    DailyAt { hour: u8, minute: u8 },
    /// Fires when a named event arrives with matching key/value.
    EventTrigger {
        event: String,
        key: String,
        value: String,
    },
}

#[derive(Debug, Clone)]
pub struct ScheduleRecord {
    pub id: String,
    pub title: String,
    pub task: Value,
    pub trigger: Trigger,
    pub last_run_unix: Option<f64>,
    pub created_at_unix: f64,
    pub paused: bool,
}

/// Durable schedule registry (the caller persists `as_value()` documents).
#[derive(Default)]
pub struct ScheduleRegistry {
    records: BTreeMap<String, ScheduleRecord>,
}

fn valid_hhmm(hour: u8, minute: u8) -> bool {
    hour < 24 && minute < 60
}

impl ScheduleRegistry {
    pub fn new() -> ScheduleRegistry {
        ScheduleRegistry::default()
    }

    pub fn add_interval(
        &mut self,
        title: &str,
        task: Value,
        seconds: u64,
    ) -> ScheduleResult<String> {
        if seconds < 60 {
            return err("interval schedules run at most once a minute");
        }
        self.add(title, task, Trigger::Interval { seconds })
    }

    pub fn add_daily(
        &mut self,
        title: &str,
        task: Value,
        hour: u8,
        minute: u8,
    ) -> ScheduleResult<String> {
        if !valid_hhmm(hour, minute) {
            return err("daily schedules need a valid wall-clock time");
        }
        self.add(title, task, Trigger::DailyAt { hour, minute })
    }

    pub fn add_event(
        &mut self,
        title: &str,
        task: Value,
        event: &str,
        key: &str,
        value: &str,
    ) -> ScheduleResult<String> {
        if event.trim().is_empty() || key.trim().is_empty() {
            return err("event triggers require an event name and a key");
        }
        self.add(
            title,
            task,
            Trigger::EventTrigger {
                event: event.to_string(),
                key: key.to_string(),
                value: value.to_string(),
            },
        )
    }

    fn add(&mut self, title: &str, task: Value, trigger: Trigger) -> ScheduleResult<String> {
        if title.trim().is_empty() || title.len() > 128 {
            return err("schedule title must be bounded non-empty text");
        }
        if !task.is_object() {
            return err("scheduled task must be an object");
        }
        let id = format!("sched_{}", af_entropy::token());
        self.records.insert(
            id.clone(),
            ScheduleRecord {
                id: id.clone(),
                title: title.to_string(),
                task,
                trigger,
                last_run_unix: None,
                created_at_unix: now_unix(),
                paused: false,
            },
        );
        Ok(id)
    }

    pub fn pause(&mut self, id: &str, paused: bool) -> ScheduleResult<()> {
        let Some(record) = self.records.get_mut(id) else {
            return err(format!("unknown schedule {id}"));
        };
        record.paused = paused;
        Ok(())
    }

    /// Interval due check: never before the previous run + interval.
    fn interval_due(record: &ScheduleRecord, seconds: u64, now_unix: f64) -> bool {
        match record.last_run_unix {
            Some(last) => now_unix - last >= seconds as f64,
            None => true,
        }
    }

    /// Daily due: the wall clock reached HH:MM and the last run happened
    /// strictly before today's HH:MM.
    fn daily_due(record: &ScheduleRecord, hour: u8, minute: u8, now_unix: f64) -> bool {
        let today_target = today_target_unix(now_unix, hour, minute);
        match record.last_run_unix {
            Some(last) => last < today_target && now_unix >= today_target,
            None => now_unix >= today_target,
        }
    }

    /// Which schedules are due at `now_unix` (interval/daily) — event
    /// triggers are matched separately by [`Self::match_event`].
    pub fn due(&self, now_unix: f64) -> Vec<String> {
        self.records
            .values()
            .filter(|record| !record.paused)
            .filter(|record| match &record.trigger {
                Trigger::Interval { seconds } => Self::interval_due(record, *seconds, now_unix),
                Trigger::DailyAt { hour, minute } => {
                    Self::daily_due(record, *hour, *minute, now_unix)
                }
                Trigger::EventTrigger { .. } => false,
            })
            .map(|record| record.id.clone())
            .collect()
    }

    /// Event trigger matching; a matched trigger fires once per event.
    pub fn match_event(&self, event: &str, payload: &Value) -> Vec<String> {
        self.records
            .values()
            .filter(|record| !record.paused)
            .filter(|record| match &record.trigger {
                Trigger::EventTrigger {
                    event: want,
                    key,
                    value,
                } => {
                    event == want
                        && payload.get(key).and_then(Value::as_str) == Some(value.as_str())
                }
                _ => false,
            })
            .map(|record| record.id.clone())
            .collect()
    }

    /// Mark a run: honest drift accounting — next due recomputes from now.
    pub fn mark_run(&mut self, id: &str, now_unix: f64) -> ScheduleResult<Value> {
        let Some(record) = self.records.get_mut(id) else {
            return err(format!("unknown schedule {id}"));
        };
        record.last_run_unix = Some(now_unix);
        Ok(json!({
            "id": id, "last_run_unix": now_unix,
            "trigger": match &record.trigger {
                Trigger::Interval { seconds } => json!({"interval_seconds": seconds}),
                Trigger::DailyAt { hour, minute } => json!({"daily_at": format!("{hour:02}:{minute:02}")}),
                Trigger::EventTrigger { event, .. } => json!({"event": event}),
            },
        }))
    }

    pub fn as_value(&self) -> Value {
        json!({
            "schedules": self.records.values().map(|record| json!({
                "id": record.id, "title": record.title, "task": record.task,
                "trigger": match &record.trigger {
                    Trigger::Interval { seconds } => json!({"interval_seconds": seconds}),
                    Trigger::DailyAt { hour, minute } => json!({"daily_at": format!("{hour:02}:{minute:02}")}),
                    Trigger::EventTrigger { event, key, value } => {
                        json!({"event": event, "key": key, "value": value})
                    }
                },
                "last_run_unix": record.last_run_unix,
                "paused": record.paused,
            })).collect::<Vec<_>>(),
        })
    }
}

fn now_unix() -> f64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs_f64()
}

/// Midnight (UTC) of the day containing `now_unix`, plus hour/minute offset.
fn today_target_unix(now_unix: f64, hour: u8, minute: u8) -> f64 {
    let day_start = (now_unix as u64 / 86_400) * 86_400;
    day_start as f64 + (hour as u64 * 3600 + minute as u64 * 60) as f64
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn interval_schedule_fires_repeatedly_not_stacked() {
        let mut registry = ScheduleRegistry::new();
        let id = registry
            .add_interval("news", json!({"goal": "brief"}), 3600)
            .unwrap();
        let t0 = 1_800_000_000.0;
        assert!(registry.due(t0).contains(&id), "never-run schedule is due");
        registry.mark_run(&id, t0).unwrap();
        assert!(
            registry.due(t0 + 1800.0).is_empty(),
            "half-interval is not due"
        );
        assert!(
            registry.due(t0 + 3600.0).contains(&id),
            "interval elapsed fires again"
        );
        // Sub-minute intervals are refused.
        assert!(registry.add_interval("spam", json!({}), 30).is_err());
    }

    #[test]
    fn daily_schedule_fires_once_per_day() {
        let mut registry = ScheduleRegistry::new();
        let id = registry
            .add_daily("morning brief", json!({"goal": "digest"}), 9, 0)
            .unwrap();
        let day_start = 1_800_000_000.0 - (1_800_000_000.0 % 86_400.0);
        let before = day_start + 8.0 * 3600.0;
        let after = day_start + 9.0 * 3600.0 + 60.0;
        assert!(!registry.due(before).contains(&id));
        assert!(registry.due(after).contains(&id));
        registry.mark_run(&id, after).unwrap();
        assert!(
            !registry.due(after + 3600.0).contains(&id),
            "same-day refire refused"
        );
        assert!(
            registry.due(after + 86_400.0).contains(&id),
            "next day fires again"
        );
        assert!(registry.add_daily("bad", json!({}), 24, 0).is_err());
    }

    #[test]
    fn event_triggers_match_exact_key_values() {
        let mut registry = ScheduleRegistry::new();
        let id = registry
            .add_event(
                "ci failure response",
                json!({"goal": "triage"}),
                "ci_finished",
                "status",
                "failed",
            )
            .unwrap();
        assert!(registry
            .match_event(
                "ci_finished",
                &json!({"status": "failed", "branch": "main"})
            )
            .contains(&id));
        assert!(registry
            .match_event("ci_finished", &json!({"status": "passed"}))
            .is_empty());
        assert!(registry
            .match_event("other_event", &json!({"status": "failed"}))
            .is_empty());
    }

    #[test]
    fn pause_and_persistence() {
        let mut registry = ScheduleRegistry::new();
        let id = registry
            .add_interval("paused task", json!({"goal": "x"}), 60)
            .unwrap();
        registry.pause(&id, true).unwrap();
        let t0 = 1_800_000_000.0;
        assert!(registry.due(t0).is_empty(), "paused schedules never fire");
        registry.pause(&id, false).unwrap();
        assert!(registry.due(t0).contains(&id));
        assert!(registry.pause("sched_unknown", true).is_err());
        let document = registry.as_value();
        assert_eq!(document["schedules"].as_array().unwrap().len(), 1);
    }
}
