//! af-graph: structural graph core for atomic-flow — port of the Python
//! draft's `graph/` package (roadmap P01/P02). Nodes/edges are validated JSON
//! objects persisted as a canonical document; identity, CAS and delta
//! semantics follow the Python contracts exactly.

pub mod adapter_ast;
pub mod adapter_resource;
pub mod canonical;
pub mod delta;
pub mod identity;
pub mod project;
pub mod relocation;
pub mod resolve;
pub mod schema;
pub mod semantic;
pub mod store;

pub use schema::{edge_id, file_id, symbol_id, GraphContractError, GRAPH_SCHEMA_VERSION};
pub use store::GraphStore;
