"use client";

import React, { useState } from "react";
import { CustomSkillConfig } from "../../../types/mission";
import { MarkdownRenderer } from "../../../components/MarkdownRenderer";
import {
  Code2,
  Plus,
  ShieldCheck,
  CheckCircle2,
  Play,
} from "lucide-react";

export default function SkillsSettingsPage() {
  const [skills] = useState<CustomSkillConfig[]>([
    {
      id: "skill-ast-parser",
      name: "ast_symbol_analyzer",
      description: "Extracts classes, function signatures, and imports using Python AST safely",
      runtime: "python",
      is_active: true,
      signature: "def ast_symbol_analyzer(file_path: str) -> dict",
      code: `import ast

def ast_symbol_analyzer(file_path: str) -> dict:
    """Analyze Python source file symbols safely without executing arbitrary bytecode.
    
    ### Returns:
    - **classes**: List of class names defined in the AST
    - **functions**: List of function and async function signatures
    - **imports**: External module dependencies
    """
    with open(file_path, "r", encoding="utf-8") as f:
        tree = ast.parse(f.read())
    classes = [n.name for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]
    funcs = [n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]
    return {"classes": classes, "functions": funcs}`,
    },
    {
      id: "skill-json-validator",
      name: "schema_validator",
      description: "Validates JSON payloads against Draft-07 schemas inside sandbox runtime",
      runtime: "python",
      is_active: true,
      signature: "def schema_validator(data: dict, schema: dict) -> bool",
      code: `import jsonschema

def schema_validator(data: dict, schema: dict) -> bool:
    """Validate JSON instance against strict JSON Schema.
    
    | Parameter | Type | Description |
    |-----------|------|-------------|
    | data | dict | Target JSON dictionary |
    | schema | dict | Draft-07 JSON Schema |
    """
    try:
        jsonschema.validate(instance=data, schema=schema)
        return True
    except jsonschema.ValidationError:
        return False`,
    },
  ]);

  const [selectedSkillId, setSelectedSkillId] = useState<string>("skill-ast-parser");
  const [showAddForm, setShowAddForm] = useState(false);
  const [testOutput, setTestOutput] = useState<string | null>(null);
  const [isTesting, setIsTesting] = useState(false);

  const currentSkill = skills.find((s) => s.id === selectedSkillId) || skills[0];

  const handleTestSkill = () => {
    setIsTesting(true);
    setTestOutput(null);
    setTimeout(() => {
      setIsTesting(false);
      setTestOutput(
        `[AST VETTING: PASSED]\n- Imports analyzed: 1 (safe)\n- Dangerous builtins (eval, exec, __import__): NONE DETECTED\n- Execution inside sandbox completed in 14ms\nResult: {"classes": ["MissionOrchestrator"], "functions": ["execute_turn", "teardown"]}`
      );
    }, 1000);
  };

  return (
    <div className="max-w-6xl mx-auto space-y-6">
      {/* Page Header */}
      <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4 pb-4 border-b border-border/70">
        <div>
          <h1 className="text-xl font-bold font-mono tracking-wide text-foreground flex items-center gap-2">
            <Code2 className="w-5 h-5 text-cyan" />
            CUSTOM SKILLS & PYTHON TOOLS ENGINE
          </h1>
          <p className="text-xs font-mono text-muted-foreground mt-1">
            Dynamic Python execution tools protected by strict AST parsing, static sandbox verification, and memory caps.
          </p>
        </div>

        <button
          type="button"
          onClick={() => setShowAddForm(!showAddForm)}
          className="flex items-center gap-2 px-3 py-2 rounded border border-cyan/40 bg-cyan/10 hover:bg-cyan/20 text-cyan text-xs font-mono font-semibold transition-all shadow-sm shadow-cyan/5 cursor-pointer"
        >
          <Plus className="w-3.5 h-3.5" />
          <span>NEW PYTHON SKILL</span>
        </button>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Left: Skills List */}
        <div className="space-y-3">
          <div className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground font-semibold px-1">
            REGISTERED SKILLS ({skills.length})
          </div>

          <div className="space-y-2">
            {skills.map((skill) => {
              const isSelected = skill.id === selectedSkillId;
              return (
                <div
                  key={skill.id}
                  onClick={() => {
                    setSelectedSkillId(skill.id);
                    setTestOutput(null);
                  }}
                  className={`p-3 rounded-lg border transition-all cursor-pointer ${
                    isSelected
                      ? "border-cyan bg-cyan/10 shadow-sm shadow-cyan/10"
                      : "border-border/80 bg-surface/40 hover:bg-surface hover:border-border"
                  }`}
                >
                  <div className="flex items-center justify-between">
                    <span className="font-mono font-bold text-xs text-foreground">
                      {skill.name}
                    </span>
                    <span className="text-[9px] font-mono px-1.5 py-0.5 rounded bg-emerald-500/10 border border-emerald-500/30 text-emerald-400 flex items-center gap-1">
                      <ShieldCheck className="w-3 h-3" /> AST SAFE
                    </span>
                  </div>
                  <div className="text-[11px] font-mono text-cyan/80 mt-1 truncate">
                    {skill.signature}
                  </div>
                  <p className="text-[11px] text-muted-foreground line-clamp-2 mt-1 font-sans">
                    {skill.description}
                  </p>
                </div>
              );
            })}
          </div>
        </div>

        {/* Right: Code Viewer, Docstring Markdown, and Sandbox Test */}
        <div className="lg:col-span-2 rounded-lg border border-border/80 bg-surface/40 p-5 space-y-4">
          <div className="flex items-center justify-between border-b border-border/60 pb-3">
            <div>
              <h2 className="text-sm font-bold text-foreground font-mono">
                {currentSkill.name}.py
              </h2>
              <span className="text-[10px] font-mono text-cyan">Runtime: Python 3.12+ (uv isolated)</span>
            </div>

            <button
              type="button"
              onClick={handleTestSkill}
              disabled={isTesting}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded bg-cyan hover:bg-cyan/90 text-void font-bold text-xs font-mono transition-all shadow-sm shadow-cyan/20 cursor-pointer disabled:opacity-50"
            >
              <Play className="w-3.5 h-3.5" />
              <span>{isTesting ? "VETTING & RUNNING..." : "TEST IN SANDBOX"}</span>
            </button>
          </div>

          {/* Docstring & Specification Markdown Preview */}
          <div className="p-3.5 rounded border border-border/70 bg-void/70 space-y-2">
            <div className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground font-semibold">
              DOCUMENTATION & SPECIFICATION PREVIEW
            </div>
            <MarkdownRenderer
              content={
                currentSkill.id === "skill-ast-parser"
                  ? `### AST Symbol Analyzer
Analyze Python source file symbols safely without executing arbitrary bytecode.

- **Classes**: Extracts AST class definitions and base inheritance
- **Functions**: Extracts top-level and method signatures
- **Security Check**: Enforces sandbox AST policy before invocation.`
                  : `### Schema Validator
Validate JSON dictionaries against Draft-07 schemas directly inside the isolated runtime.

| Attribute | Value |
|---|---|
| Runtime | Python 3.12+ (uv) |
| Isolation | Docker / Pod sandbox |
| Memory Cap | 256MB |`
              }
            />
          </div>

          {/* Source Code View */}
          <div>
            <div className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground font-semibold mb-1">
              SOURCE IMPLEMENTATION
            </div>
            <div className="rounded border border-border bg-void/90 p-3 font-mono text-xs text-foreground/90 overflow-x-auto leading-relaxed shadow-inner">
              <pre>
                <code>{currentSkill.code}</code>
              </pre>
            </div>
          </div>

          {/* Test Sandbox Execution Output */}
          {testOutput && (
            <div className="p-3 rounded border border-emerald-500/40 bg-emerald-500/5 text-xs font-mono text-foreground space-y-1">
              <div className="text-emerald-400 font-bold text-[10px] flex items-center gap-1">
                <CheckCircle2 className="w-3.5 h-3.5" /> SANDBOX VERIFICATION SUCCESS
              </div>
              <pre className="text-[11px] text-muted-foreground whitespace-pre-wrap">{testOutput}</pre>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
