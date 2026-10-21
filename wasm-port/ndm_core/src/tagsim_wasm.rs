//! wasm-bindgen boundary for the tag-aware kernel.
//!
//! Entries:
//!   * `runSaTagged(input)`   — full SA over a serde-deserialized run spec.
//!   * `scoreTagged(input)`   — score-only pass on a fixed assignment.
//!   * `explainTagged(input)` — per-slot breakdown of a fixed assignment (rework scoring only).
//!   * `lootEval(input)`      — the loot objective at given deck IQ / IR totals (result readouts).
//!
//! Every list-y field defaults to empty so older payloads stay loadable. Unknown names (card types, colours, groups,
//! cores, rule axes, implicit kinds) and malformed value models come back as errors, never panics: a panic traps the
//! WASM instance.

use serde::{Deserialize, Serialize};
use wasm_bindgen::prelude::*;

use crate::tagsim::{
    explain_tagged, group_bits_to_strs, run_sa_tagged as run_core, score_tagged as score_core,
    tag_card_type_to_str, tag_color_to_str, try_group_bit_from_str, try_rw_core_from_str, try_tag_card_type_from_str,
    try_tag_color_from_str, try_tag_core_from_str, CardSpec, CoreSpecIn, Curve, Implicit, LootTerm, Phase2, Rework,
    RwCore, SaSchedule, SearchOpts, SlotCard, TagAxis, TagRule, TagRun, TagSimConfig, ValueChannel, ValueModel,
    ValueTerm, COLOR_NONE, MAX_VALUE_ATTRS, NO_ATTR, SCHEDULE_LEGACY,
};

#[derive(Deserialize, Clone)]
pub struct StackIn {
    pub t: String,
    pub color: String,
    #[serde(default)]
    pub scale_color: String,
    #[serde(default)]
    pub groups: Vec<String>,
    /// null / missing = unlimited supply.
    #[serde(default)]
    pub count: Option<u32>,
    #[serde(default)]
    pub min_place: u32,
}

#[derive(Deserialize, Clone)]
pub struct RuleIn {
    pub axis: String,     // "color" | "type" | "group" | "greed"
    #[serde(default)]
    pub key: String,
    #[serde(default)]
    pub min: u32,
    /// null / missing = uncapped.
    #[serde(default)]
    pub max: Option<u32>,
}

#[derive(Deserialize, Clone)]
pub struct ImplicitIn {
    pub kind: String,
    #[serde(default)]
    pub value: f64,
    #[serde(default)]
    pub groups: Vec<String>,
    #[serde(default)]
    pub colors: Vec<String>,
    /// ptype for "freq"; range ("column"/"surrounding") for "adjacency".
    #[serde(default)]
    pub extra: String,
}

/// Rework core scoring: cores as (name, value, k, colour or ""), Nitwit / Villager bonus slots and value.
#[derive(Deserialize, Clone)]
pub struct ReworkIn {
    pub cores: Vec<(String, f64, usize, String)>,
    #[serde(default)]
    pub nitwit_slots: Vec<usize>,
    #[serde(default)]
    pub nitwit_value: f64,
}

#[derive(Deserialize, Clone)]
pub struct CurveIn {
    pub x0: f64,
    pub dx: f64,
    pub ys: Vec<f64>,
}

/// A channel: base plus sparse per-attribute curves (attribute index, curve); missing attributes are zero curves.
#[derive(Deserialize, Clone)]
pub struct ChannelIn {
    pub base: f64,
    #[serde(default)]
    pub curves: Vec<(usize, CurveIn)>,
}

#[derive(Deserialize, Clone)]
pub struct LootIn {
    pub iq_attr: usize,
    pub ir_attr: usize,
    pub base_q: f64,
    pub base_r: f64,
    pub base_rolls: Vec<f64>,
    pub chests: Vec<[f64; 5]>,
    pub mythic_weight: f64,
    pub paths: Vec<(f64, f64)>,
    pub scale: f64,
}

#[derive(Deserialize, Clone)]
pub struct TermIn {
    pub weight: f64,
    #[serde(default)]
    pub channels: Vec<ChannelIn>,
    #[serde(default)]
    pub extra: Vec<(usize, CurveIn)>,
    #[serde(default)]
    pub loot: Option<LootIn>,
}

/// Build-weighted objective: per stack its value and attribute (-1 = none) and whether it is an unscaled level card.
#[derive(Deserialize, Clone)]
pub struct ValueModelIn {
    pub stack_value: Vec<f64>,
    pub stack_attr: Vec<i32>,
    #[serde(default)]
    pub stack_unscaled: Vec<bool>,
    pub n_attr: usize,
    pub terms: Vec<TermIn>,
}

#[derive(Deserialize, Clone)]
pub struct ScheduleIn {
    pub kind: String,
    pub params: Vec<f64>,
}

#[derive(Deserialize, Clone)]
pub struct Phase2In {
    pub top_k: usize,
    pub n_iter: usize,
    pub schedule: ScheduleIn,
}

#[derive(Deserialize, Clone)]
pub struct TagRunInput {
    pub slots: Vec<(i32, i32)>,
    pub row_peers: Vec<Vec<usize>>,
    pub col_peers: Vec<Vec<usize>>,
    pub surr_peers: Vec<Vec<usize>>,
    pub diag_peers: Vec<Vec<usize>>,
    #[serde(default)]
    pub arcane_slot_indices: Vec<usize>,
    #[serde(default)]
    pub stacks: Vec<StackIn>,
    #[serde(default)]
    pub tag_rules: Vec<RuleIn>,
    #[serde(default)]
    pub blanket_groups: Vec<String>,
    #[serde(default)]
    pub assignable_groups: Vec<String>,
    /// Category-tag combos that exist on real cards; a card's category set
    /// must stay a subset of one. Empty = unconstrained.
    #[serde(default)]
    pub legal_combos: Vec<Vec<String>>,
    #[serde(default)]
    pub implicits: Vec<ImplicitIn>,
    /// Each entry: [core_type, color_or_empty, override_or_negative].
    #[serde(default)]
    pub cores: Vec<(String, String, f64)>,
    #[serde(default)]
    pub min_stat_placed: u32,
    #[serde(default)]
    pub final_pass_nonfoil_evo: bool,
    #[serde(default)]
    pub exact_groups: bool,
    pub n_iter: usize,
    pub restarts: usize,
    #[serde(default)]
    pub seed: Option<u64>,
    #[serde(default)]
    pub schedule: Option<ScheduleIn>,
    #[serde(default)]
    pub phase2: Option<Phase2In>,
    #[serde(default)]
    pub polish: bool,
    #[serde(default)]
    pub rework: Option<ReworkIn>,
    #[serde(default)]
    pub value_model: Option<ValueModelIn>,

    pub mult_dir_vert: f64,
    pub mult_dir_horiz: f64,
    pub mult_pure_base: f64,
    pub mult_pure_scale: f64,
    pub mult_equilibrium: f64,
    pub mult_foil: f64,
    pub mult_steadfast: f64,
    pub mult_sparkling: f64,
    pub mult_color: f64,
    pub mult_deluxe_flat: f64,
    pub mult_deluxe_core_base: f64,
    pub mult_deluxe_core_scale: f64,
    #[serde(default)]
    pub mult_void_core_base: f64,
    #[serde(default)]
    pub mult_void_core_scale: f64,
    #[serde(default = "default_one")]
    pub mult_archive_core: f64,
    pub greed_additive: bool,
    pub additive_cores: bool,
    pub is_shiny: bool,
    #[serde(default = "default_true")]
    pub auto_place_arcane: bool,
    #[serde(default)]
    pub colors_real: bool,
    #[serde(default)]
    pub complex_cards: bool,
    #[serde(default = "default_true")]
    pub wv_foil_rules: bool,
    #[serde(default = "default_true")]
    pub floor_counts_deluxe: bool,

    /// scoreTagged / explainTagged only: fixed assignment, parallel to `slots`.
    /// Each entry: [type, color, scale_color, groups[]].
    #[serde(default)]
    pub assignment: Vec<(String, String, String, Vec<String>)>,
    /// scoreTagged / explainTagged only: each assigned card's stack (-1 = none); needed by the value model.
    #[serde(default)]
    pub stack_index: Vec<i32>,
}

fn default_true() -> bool { true }
fn default_one() -> f64 { 1.0 }

#[derive(Serialize)]
pub struct PlacedOut {
    pub t: String,
    pub color: String,
    pub scale_color: String,
    pub groups: Vec<String>,
    /// The stack this card came from (-1 = none, e.g. a dead slot).
    pub stack: i32,
}

#[derive(Serialize)]
pub struct TagRunResult {
    pub assignment: Vec<PlacedOut>,
    pub score: f64,
}

#[derive(Serialize)]
pub struct SlotPartsOut {
    pub term: f64,
    pub base: f64,
    pub adds: Vec<(u16, f64)>,
    pub mults: Vec<(u16, f64)>,
    pub boost: f64,
    pub empyreal: f64,
}

#[derive(Serialize)]
pub struct ExplainResult {
    pub score: f64,
    pub slots: Vec<SlotPartsOut>,
}

#[derive(Deserialize)]
pub struct LootEvalIn {
    pub loot: LootIn,
    pub iq: f64,
    pub ir: f64,
}

#[derive(Serialize)]
pub struct LootEvalOut {
    pub score: f64,
    pub path: usize,
}

type R<T> = Result<T, String>;

fn color(s: &str) -> R<u8> { try_tag_color_from_str(s).ok_or_else(|| format!("unknown color: {s:?}")) }
fn card_type(s: &str) -> R<u8> { try_tag_card_type_from_str(s).ok_or_else(|| format!("unknown card type: {s:?}")) }

fn groups(gs: &[String]) -> R<u16> {
    let mut mask = 0u16;
    for g in gs {
        mask |= try_group_bit_from_str(g).ok_or_else(|| format!("unknown group: {g:?}"))?;
    }
    Ok(mask)
}

fn curve(c: &CurveIn) -> R<Curve> {
    if !c.ys.is_empty() && !(c.dx > 0.0) {
        return Err("value_model: a non-empty curve needs dx > 0".into());
    }
    Ok(Curve { x0: c.x0, dx: c.dx, ys: c.ys.clone() })
}

fn dense(sparse: &[(usize, CurveIn)], n_attr: usize) -> R<Vec<Curve>> {
    let mut out: Vec<Curve> = (0..n_attr).map(|_| Curve { x0: 0.0, dx: 1.0, ys: Vec::new() }).collect();
    for (a, c) in sparse {
        if *a >= n_attr {
            return Err(format!("value_model: curve attribute {a} out of range ({n_attr})"));
        }
        out[*a] = curve(c)?;
    }
    Ok(out)
}

fn loot_term(l: &LootIn, n_attr: usize) -> R<LootTerm> {
    if l.iq_attr >= n_attr || l.ir_attr >= n_attr {
        return Err("loot: attribute index out of range".into());
    }
    if l.base_rolls.is_empty() || l.chests.is_empty() || l.paths.is_empty() {
        return Err("loot: base_rolls, chests and paths must be non-empty".into());
    }
    Ok(LootTerm {
        iq_attr: l.iq_attr, ir_attr: l.ir_attr, base_q: l.base_q, base_r: l.base_r, base_rolls: l.base_rolls.clone(),
        chests: l.chests.clone(), mythic_weight: l.mythic_weight, paths: l.paths.clone(), scale: l.scale,
    })
}

fn value_model(vm: &ValueModelIn, n_stacks: usize) -> R<ValueModel> {
    if vm.stack_value.len() != n_stacks || vm.stack_attr.len() != n_stacks {
        return Err(format!("value_model: {} values / {} attrs for {} stacks",
                           vm.stack_value.len(), vm.stack_attr.len(), n_stacks));
    }
    if !vm.stack_unscaled.is_empty() && vm.stack_unscaled.len() != n_stacks {
        return Err("value_model: stack_unscaled length != stacks".into());
    }
    if vm.n_attr > MAX_VALUE_ATTRS {
        return Err(format!("value_model: {} attributes, max {}", vm.n_attr, MAX_VALUE_ATTRS));
    }
    if let Some(a) = vm.stack_attr.iter().find(|&&a| a >= vm.n_attr as i32) {
        return Err(format!("value_model: attribute index {a} out of range"));
    }
    let mut terms = Vec::with_capacity(vm.terms.len());
    for t in &vm.terms {
        let mut channels = Vec::with_capacity(t.channels.len());
        for ch in &t.channels {
            channels.push(ValueChannel { base: ch.base, curves: dense(&ch.curves, vm.n_attr)? });
        }
        let extra = if t.extra.is_empty() { Vec::new() } else { dense(&t.extra, vm.n_attr)? };
        let loot = match &t.loot { Some(l) => Some(loot_term(l, vm.n_attr)?), None => None };
        terms.push(ValueTerm { weight: t.weight, channels, extra, loot });
    }
    Ok(ValueModel {
        stack_value: vm.stack_value.clone(),
        stack_attr: vm.stack_attr.iter().map(|&a| if a < 0 { NO_ATTR } else { a as u8 }).collect(),
        stack_unscaled: vm.stack_unscaled.clone(),
        terms,
    })
}

fn schedule(s: &ScheduleIn) -> R<SaSchedule> {
    match (s.kind.as_str(), s.params.as_slice()) {
        ("geometric", &[t0, t1]) => Ok(SaSchedule::Geometric { t0, t1 }),
        ("target", &[a0, a1, t_init, eta]) => Ok(SaSchedule::Target { a0, a1, t_init, eta }),
        (k, p) => Err(format!("bad schedule {k:?} {p:?}: want geometric [t0, t1] or target [a0, a1, t_init, eta]")),
    }
}

fn rework(rw: &ReworkIn, slots: &[(i32, i32)]) -> R<Rework> {
    let mut cores = Vec::with_capacity(rw.cores.len());
    for (name, value, k, col) in &rw.cores {
        cores.push(RwCore {
            kind: try_rw_core_from_str(name).ok_or_else(|| format!("unknown rework core: {name:?}"))?,
            value: *value,
            k: *k,
            color: if col.is_empty() { COLOR_NONE } else { color(col)? },
        });
    }
    if let Some(&i) = rw.nitwit_slots.iter().find(|&&i| i >= slots.len()) {
        return Err(format!("rework: nitwit slot {i} out of range"));
    }
    Ok(Rework { cores, nitwit_value: rw.nitwit_value, ..Default::default() }.prepare(slots, &rw.nitwit_slots))
}

pub(crate) fn build_run(inp: &TagRunInput) -> R<TagRun<'_>> {
    let mut stacks = Vec::with_capacity(inp.stacks.len());
    for s in &inp.stacks {
        let c = color(&s.color)?;
        stacks.push(CardSpec {
            t: card_type(&s.t)?,
            color: c,
            scale: if s.scale_color.is_empty() { c } else { color(&s.scale_color)? },
            groups: groups(&s.groups)?,
            count: s.count.unwrap_or(u32::MAX),
            min_place: s.min_place,
        });
    }

    let mut tag_rules = Vec::with_capacity(inp.tag_rules.len());
    for r in &inp.tag_rules {
        let (axis, key) = match r.axis.as_str() {
            "color" => (TagAxis::Color, color(&r.key)? as u16),
            "type" => (TagAxis::CardType, card_type(&r.key)? as u16),
            "group" => (TagAxis::Group, groups(std::slice::from_ref(&r.key))?),
            "greed" => (TagAxis::GreedTotal, 0u16),
            other => return Err(format!("unknown rule axis: {other:?}")),
        };
        tag_rules.push(TagRule { axis, key, min: r.min, max: r.max.unwrap_or(u32::MAX) });
    }

    let mut implicits = Vec::with_capacity(inp.implicits.len());
    for im in &inp.implicits {
        let gmask = groups(&im.groups)?;
        let mut cmask = 0u8;
        for c in &im.colors { cmask |= 1 << color(c)?; }
        implicits.push(match im.kind.as_str() {
            "global" => Implicit::GlobalFlat { value: im.value, groups: gmask, colors: cmask },
            "freq" => Implicit::Freq { mult: im.value, ptype: card_type(&im.extra)? },
            "adjacency" => Implicit::Adjacency { value: im.value, group: gmask, surrounding: im.extra == "surrounding" },
            "color_mismatch" => Implicit::ColorMismatch { value: im.value },
            "row_pos" => Implicit::RowPos { value: im.value },
            "chain" => Implicit::Chain { value: im.value },
            "empty_slots" => Implicit::EmptySlots { value: im.value },
            "unique_groups" => Implicit::UniqueGroups { value: im.value },
            "mirror" => Implicit::Mirror { value: im.value },
            "gameplay" | "mystery" => continue,
            other => return Err(format!("unknown implicit kind: {other:?}")),
        });
    }

    let mut cores = Vec::with_capacity(inp.cores.len());
    for (t, c, o) in &inp.cores {
        cores.push(CoreSpecIn {
            core_type: try_tag_core_from_str(t).ok_or_else(|| format!("unknown core type: {t:?}"))?,
            color: if c.is_empty() { COLOR_NONE } else { color(c)? },
            override_: *o,
        });
    }

    let n_slots = inp.slots.len();
    for (name, peers) in [("row", &inp.row_peers), ("col", &inp.col_peers), ("surr", &inp.surr_peers),
                          ("diag", &inp.diag_peers)] {
        if peers.len() != n_slots || peers.iter().flatten().any(|&q| q >= n_slots) {
            return Err(format!("{name}_peers does not match the {n_slots} slots"));
        }
    }

    let value_model = match &inp.value_model { Some(vm) => Some(value_model(vm, stacks.len())?), None => None };
    let rework = match &inp.rework { Some(rw) => Some(rework(rw, &inp.slots)?), None => None };
    let sched = match &inp.schedule { Some(s) => schedule(s)?, None => SCHEDULE_LEGACY };
    let phase2 = match &inp.phase2 {
        Some(p) => Some(Phase2 { top_k: p.top_k, n_iter: p.n_iter, sched: schedule(&p.schedule)? }),
        None => None,
    };

    Ok(TagRun {
        slots: &inp.slots,
        row_peers: inp.row_peers.clone(),
        col_peers: inp.col_peers.clone(),
        surr_peers: inp.surr_peers.clone(),
        diag_peers: inp.diag_peers.clone(),
        arcane_slot_indices: inp.arcane_slot_indices.clone(),
        stacks, tag_rules,
        blanket_groups: groups(&inp.blanket_groups)?,
        assignable_groups: groups(&inp.assignable_groups)?,
        legal_combos: inp.legal_combos.iter().map(|c| groups(c)).collect::<R<Vec<u16>>>()?,
        implicits, cores,
        min_stat_placed: inp.min_stat_placed,
        final_pass_nonfoil_evo: inp.final_pass_nonfoil_evo,
        exact_groups: inp.exact_groups,
        n_iter: inp.n_iter,
        restarts: inp.restarts,
        seed: inp.seed,
        sched,
        opts: SearchOpts { polish: inp.polish, phase2, ..SearchOpts::default() },
        value_model,
        rework,
        cfg: TagSimConfig {
            mult_dir_vert: inp.mult_dir_vert,
            mult_dir_horiz: inp.mult_dir_horiz,
            mult_pure_base: inp.mult_pure_base,
            mult_pure_scale: inp.mult_pure_scale,
            mult_equilibrium: inp.mult_equilibrium,
            mult_foil: inp.mult_foil,
            mult_steadfast: inp.mult_steadfast,
            mult_sparkling: inp.mult_sparkling,
            mult_color: inp.mult_color,
            mult_deluxe_flat: inp.mult_deluxe_flat,
            mult_deluxe_core_base: inp.mult_deluxe_core_base,
            mult_deluxe_core_scale: inp.mult_deluxe_core_scale,
            mult_void_core_base: inp.mult_void_core_base,
            mult_void_core_scale: inp.mult_void_core_scale,
            mult_archive_core: inp.mult_archive_core,
            greed_additive: inp.greed_additive,
            additive_cores: inp.additive_cores,
            is_shiny: inp.is_shiny,
            auto_place_arcane: inp.auto_place_arcane,
            colors_real: inp.colors_real,
            complex: inp.complex_cards,
            wv_foil_rules: inp.wv_foil_rules,
            floor_counts_deluxe: inp.floor_counts_deluxe,
        },
    })
}

pub(crate) fn placed_out(asgn: &[SlotCard]) -> Vec<PlacedOut> {
    asgn.iter().map(|c| PlacedOut {
        t: tag_card_type_to_str(c.t).to_owned(),
        color: tag_color_to_str(c.color).to_owned(),
        scale_color: tag_color_to_str(c.scale).to_owned(),
        groups: group_bits_to_strs(c.groups).iter().map(|s| s.to_string()).collect(),
        stack: if c.stack == u16::MAX { -1 } else { c.stack as i32 },
    }).collect()
}

fn fixed_assignment(inp: &TagRunInput) -> R<Vec<SlotCard>> {
    if inp.assignment.len() != inp.slots.len() {
        return Err(format!("assignment has {} cards for {} slots", inp.assignment.len(), inp.slots.len()));
    }
    if !inp.stack_index.is_empty() && inp.stack_index.len() != inp.assignment.len() {
        return Err("stack_index length != assignment".into());
    }
    if inp.value_model.is_some() && inp.stack_index.is_empty() {
        return Err("a value model needs stack_index".into());
    }
    let mut out = Vec::with_capacity(inp.assignment.len());
    for (i, (t, c, sc, gs)) in inp.assignment.iter().enumerate() {
        let col = color(c)?;
        let si = inp.stack_index.get(i).copied().unwrap_or(-1);
        out.push(SlotCard {
            t: card_type(t)?,
            color: col,
            scale: if sc.is_empty() { col } else { color(sc)? },
            groups: groups(gs)?,
            stack: if si < 0 { u16::MAX } else { si as u16 },
        });
    }
    Ok(out)
}

fn err(m: String) -> JsValue { JsValue::from_str(&format!("tagsim: {m}")) }

fn parse<T: for<'de> Deserialize<'de>>(input: JsValue) -> Result<T, JsValue> {
    serde_wasm_bindgen::from_value(input).map_err(|e| err(format!("input deserialize failed: {e}")))
}

fn out<T: Serialize>(v: &T) -> Result<JsValue, JsValue> {
    serde_wasm_bindgen::to_value(v).map_err(|e| err(format!("output serialize failed: {e}")))
}

#[wasm_bindgen(js_name = runSaTagged)]
pub fn run_sa_tagged_wasm(input: JsValue) -> Result<JsValue, JsValue> {
    let inp: TagRunInput = parse(input)?;
    let run = build_run(&inp).map_err(err)?;
    let (asgn, score) = run_core(run);
    out(&TagRunResult { assignment: placed_out(&asgn), score })
}

#[wasm_bindgen(js_name = scoreTagged)]
pub fn score_tagged_wasm(input: JsValue) -> Result<JsValue, JsValue> {
    let inp: TagRunInput = parse(input)?;
    let run = build_run(&inp).map_err(err)?;
    let asgn = fixed_assignment(&inp).map_err(err)?;
    out(&score_core(&run, &asgn))
}

#[wasm_bindgen(js_name = explainTagged)]
pub fn explain_tagged_wasm(input: JsValue) -> Result<JsValue, JsValue> {
    let inp: TagRunInput = parse(input)?;
    let run = build_run(&inp).map_err(err)?;
    let asgn = fixed_assignment(&inp).map_err(err)?;
    let (score, parts) = explain_tagged(&run, &asgn).ok_or_else(|| err("explain needs the rework scoring path".into()))?;
    out(&ExplainResult {
        score,
        slots: parts.into_iter().map(|p| SlotPartsOut {
            term: p.term, base: p.base, adds: p.adds, mults: p.mults, boost: p.boost, empyreal: p.empyreal,
        }).collect(),
    })
}

#[wasm_bindgen(js_name = lootEval)]
pub fn loot_eval_wasm(input: JsValue) -> Result<JsValue, JsValue> {
    let inp: LootEvalIn = parse(input)?;
    let term = loot_term(&inp.loot, inp.loot.iq_attr.max(inp.loot.ir_attr) + 1).map_err(err)?;
    let (score, path) = term.eval(inp.iq, inp.ir);
    out(&LootEvalOut { score, path })
}
