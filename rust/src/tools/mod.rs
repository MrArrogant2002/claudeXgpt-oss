//! Self-describing tools + registry. The three tools form the funnel:
//! glob (broad) -> grep (narrow) -> read (deep).

pub mod glob;
pub mod grep;
pub mod read;

use openai_harmony::chat::ToolDescription;
use serde_json::Value;

use crate::harmony;
use crate::sandbox::Sandbox;

pub trait Tool {
    fn name(&self) -> &str;
    fn description(&self) -> &str;
    fn parameters(&self) -> Value;
    fn run(&self, args: &Value, sandbox: &Sandbox) -> String;
}

pub struct Registry {
    tools: Vec<Box<dyn Tool>>,
}

impl Registry {
    pub fn new() -> Self {
        Self { tools: Vec::new() }
    }

    pub fn register(&mut self, t: Box<dyn Tool>) {
        self.tools.push(t);
    }

    pub fn get(&self, name: &str) -> Option<&dyn Tool> {
        self.tools.iter().find(|t| t.name() == name).map(|b| b.as_ref())
    }

    pub fn harmony_tools(&self) -> Vec<ToolDescription> {
        self.tools
            .iter()
            .map(|t| harmony::tool_description(t.name(), t.description(), t.parameters()))
            .collect()
    }
}

pub fn default_registry() -> Registry {
    let mut r = Registry::new();
    r.register(Box::new(glob::GlobTool)); // broad
    r.register(Box::new(grep::GrepTool)); // narrow
    r.register(Box::new(read::ReadTool)); // deep
    r
}
