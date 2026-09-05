"use client";

import { useEffect, useMemo, useState } from "react";
import {
  Area, AreaChart, Bar, BarChart, CartesianGrid, Legend, Line, LineChart,
  ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import {
  ArrowDownRight, ArrowUpRight, BookOpen, CalendarDays, ChevronRight,
  CircleAlert, Database, ExternalLink, FileCheck2, Layers3, Search, ShieldCheck,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import {
  Combobox, ComboboxContent, ComboboxEmpty, ComboboxInput, ComboboxItem, ComboboxList,
} from "@/components/ui/combobox";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";

type Metric = "price" | "fvCost";
type Meta = {
  generatedAt: string; coverageStart: string | null; coverageEnd: string | null;
  counts: Record<string, number>; latestSnapshotPositions: number;
  currentMedianPrice: number | null; crossHolderMatches: number;
  searchableIssuers: number;
  structuredCoverageStarts: string; legacyCoverageTargetStarts: string;
  packages: Array<{ package_name: string; source_url: string; source_type: string;
    period_start: string | null; period_end: string | null; row_count: number; warning_count: number }>;
};
type GroupRow = { holder?: string; industry?: string; positions: number; issuers: number;
  fairValue: number; medianPrice: number | null; weightedPrice: number | null;
  weightedFvCost: number | null; below90Share: number | null; pikShare: number };
type HolderMark = { holder: string; cik: string; mark: number; fairValue: number; basis: string; filingUrl: string };
type Divergence = { instrumentId: string; quarter: string; range: number; median: number;
  issuer: string; issuerId: string; instrument: string; maturity: string | null;
  matchConfidence: number; holders: HolderMark[];
  history: Array<{ quarter: string; range: number; median: number; holders: HolderMark[] }> };
type Position = { id: string; issuerId: string; issuer: string; instrumentId: string;
  instrument: string; holderCik: string; holder: string; date: string; quarter: string;
  industry: string | null; principal: number | null; cost: number | null; fairValue: number;
  price: number | null; fvCost: number | null; allInBps: number | null; pikBps: number | null;
  maturity: string | null; type: string; seniority: string | null; lien: string | null;
  matchConfidence: number; sourceConfidence: number; sourceFormat: string; filingUrl: string };
type SearchItem = { id: string; name: string; industry: string | null; positions: number;
  holders: number; lastReport: string; bucket: string };
type Overview = {
  trend: Array<{ quarter: string; positions: number; holders: number; medianPrice: number | null;
    weightedPrice: number | null; medianFvCost: number | null; weightedFvCost: number | null;
    pikShare: number; below90Share: number }>;
  holders: GroupRow[]; industries: GroupRow[];
  maturity: Array<{ bucket: string; positions: number; fairValue: number }>;
  divergence: Divergence[]; riskPositions: Position[];
};

const compact = new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 1 });
const money = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", notation: "compact", maximumFractionDigits: 1 });
const pct = new Intl.NumberFormat("en-US", { minimumFractionDigits: 1, maximumFractionDigits: 1 });
const colors = ["#7dd3fc", "#fbbf24", "#a78bfa", "#34d399", "#fb7185", "#60a5fa", "#f472b6", "#c4b5fd"];

function metricValue(position: Position, metric: Metric) { return position[metric]; }
function fmtMark(value: number | null | undefined) { return value == null ? "—" : pct.format(value); }
function markTone(value: number | null | undefined) {
  if (value == null) return "text-slate-500";
  if (value < 80) return "text-rose-300";
  if (value < 95) return "text-amber-300";
  if (value > 101) return "text-sky-300";
  return "text-slate-100";
}
function Kpi({ label, value, detail, icon: Icon }: { label: string; value: string; detail: string; icon: typeof Database }) {
  return <div className="metric-card"><div className="flex items-start justify-between gap-3"><span className="eyebrow">{label}</span><Icon className="size-4 text-sky-300/80" /></div><div className="mt-4 font-mono text-[1.8rem] font-semibold tracking-[-0.06em] text-white">{value}</div><p className="mt-1 text-xs leading-5 text-slate-400">{detail}</p></div>;
}
function PanelTitle({ title, kicker, action }: { title: string; kicker?: string; action?: React.ReactNode }) {
  return <div className="flex flex-wrap items-start justify-between gap-3 border-b border-white/8 px-5 py-4"><div>{kicker ? <p className="eyebrow mb-1">{kicker}</p> : null}<h2 className="text-base font-semibold tracking-tight text-slate-100">{title}</h2></div>{action}</div>;
}
function DataTooltip({ active, payload, label }: { active?: boolean; payload?: Array<{ name: string; value: number; color: string }>; label?: string }) {
  if (!active || !payload?.length) return null;
  return <div className="rounded-lg border border-white/10 bg-[#0d1624]/95 px-3 py-2 shadow-2xl backdrop-blur"><div className="mb-1 text-xs font-medium text-slate-300">{label}</div>{payload.map((item) => <div key={item.name} className="flex min-w-36 items-center justify-between gap-5 text-xs"><span style={{ color: item.color }}>{item.name}</span><span className="font-mono text-slate-100">{pct.format(item.value)}</span></div>)}</div>;
}
function Loading() {
  return <main className="grid min-h-screen place-items-center bg-[#07101c] text-slate-200"><div className="flex items-center gap-3 text-sm"><span className="size-2 animate-pulse rounded-full bg-sky-300" />Opening the loan tape…</div></main>;
}

export function Explorer() {
  const [meta, setMeta] = useState<Meta | null>(null);
  const [overview, setOverview] = useState<Overview | null>(null);
  const [searchItems, setSearchItems] = useState<SearchItem[]>([]);
  const [selectedIssuer, setSelectedIssuer] = useState<SearchItem | null>(null);
  const [issuerPositions, setIssuerPositions] = useState<Position[]>([]);
  const [metric, setMetric] = useState<Metric>("price");
  const [grouping, setGrouping] = useState<"holder" | "industry">("holder");
  const [activeTab, setActiveTab] = useState("market");
  const [selectedDivergence, setSelectedDivergence] = useState<Divergence | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  useEffect(() => {
    Promise.all([fetch("/data/meta.json").then(r => r.json()), fetch("/data/overview.json").then(r => r.json()), fetch("/data/search-index.json").then(r => r.json())])
      .then(([m, o, s]) => { setMeta(m); setOverview(o); setSearchItems(s); })
      .catch(() => setLoadError("The data snapshot could not be opened."));
  }, []);
  useEffect(() => {
    if (!selectedIssuer) { setIssuerPositions([]); return; }
    fetch(`/data/issuers/${selectedIssuer.bucket}.json`).then(r => r.json())
      .then((rows: Position[]) => { setIssuerPositions(rows.filter(p => p.issuerId === selectedIssuer.id)); setActiveTab("issuer"); })
      .catch(() => setLoadError("That issuer history could not be opened."));
  }, [selectedIssuer]);

  const issuerChart = useMemo(() => {
    const grouped = new Map<string, Record<string, string | number>>();
    issuerPositions.forEach(position => {
      const value = metricValue(position, metric);
      if (value == null || value < 0 || value > 200) return;
      if (!grouped.has(position.quarter)) grouped.set(position.quarter, { quarter: position.quarter });
      grouped.get(position.quarter)![position.holder] = value;
    });
    return [...grouped.values()].sort((a, b) => String(a.quarter).localeCompare(String(b.quarter)));
  }, [issuerPositions, metric]);
  const issuerHolders = useMemo(() => [...new Set(issuerPositions.map(p => p.holder))].slice(0, 8), [issuerPositions]);

  if (loadError) return <main className="grid min-h-screen place-items-center bg-[#07101c] px-6 text-slate-100"><div className="max-w-md rounded-2xl border border-rose-300/20 bg-rose-300/5 p-6"><CircleAlert className="mb-4 size-5 text-rose-300" /><h1 className="text-lg font-semibold">Snapshot unavailable</h1><p className="mt-2 text-sm text-slate-400">{loadError}</p></div></main>;
  if (!meta || !overview) return <Loading />;

  const latestTrend = overview.trend.at(-1), previousTrend = overview.trend.at(-2);
  const trendDelta = latestTrend?.weightedPrice != null && previousTrend?.weightedPrice != null ? latestTrend.weightedPrice - previousTrend.weightedPrice : null;
  const groupedRows = grouping === "holder" ? overview.holders : overview.industries;
  const metricLabel = metric === "price" ? "FV / principal" : "FV / amortized cost";

  return <main className="min-h-screen bg-[#07101c] text-slate-200">
    <header className="topbar"><div className="mx-auto flex max-w-[1600px] flex-wrap items-center justify-between gap-4 px-4 py-3 sm:px-6 lg:px-8"><div className="flex items-center gap-3"><div className="grid size-9 place-items-center rounded-lg border border-sky-300/20 bg-sky-300/8"><Layers3 className="size-[18px] text-sky-300" /></div><div><div className="text-sm font-semibold tracking-tight text-white">BDC Loan Marks</div><div className="text-[11px] text-slate-500">EDGAR Schedule of Investments</div></div></div><div className="flex items-center gap-2 text-xs text-slate-400"><span className="size-1.5 rounded-full bg-emerald-300 shadow-[0_0_12px_rgba(110,231,183,.7)]" />Snapshot through {meta.coverageEnd ?? "—"}</div></div></header>

    <div className="mx-auto max-w-[1600px] px-4 pb-16 pt-7 sm:px-6 lg:px-8">
      <section className="mb-6 grid gap-5 xl:grid-cols-[minmax(0,1fr)_520px] xl:items-end"><div><p className="eyebrow mb-3">Private credit valuation tape</p><h1 className="max-w-3xl text-3xl font-semibold tracking-[-0.04em] text-white sm:text-4xl">See the same loan through every BDC&apos;s marks.</h1><p className="mt-3 max-w-2xl text-sm leading-6 text-slate-400">Search disclosed loans, compare holders, and track fair-value dispersion by issuer, industry, and maturity.</p></div><div className="search-shell"><Search className="ml-3 size-4 shrink-0 text-slate-500" /><Combobox items={searchItems} value={selectedIssuer} onValueChange={value => setSelectedIssuer(value as SearchItem | null)} itemToStringValue={item => item?.name ?? ""}><ComboboxInput className="h-12 flex-1 border-0 bg-transparent shadow-none focus-within:ring-0" placeholder="Search an issuer…" showTrigger={false} showClear /><ComboboxContent className="border-white/10 bg-[#0d1725] text-slate-100"><ComboboxEmpty>No issuer found</ComboboxEmpty><ComboboxList>{(item: SearchItem) => <ComboboxItem key={item.id} value={item} className="py-2.5 data-highlighted:bg-sky-300/10"><div className="min-w-0 flex-1"><div className="truncate text-sm font-medium">{item.name}</div><div className="mt-0.5 truncate text-xs text-slate-500">{item.industry ?? "Unclassified"} · {item.holders} holder{item.holders === 1 ? "" : "s"}</div></div></ComboboxItem>}</ComboboxList></ComboboxContent></Combobox><kbd className="mr-3 hidden rounded border border-white/10 bg-white/5 px-2 py-1 font-mono text-[10px] text-slate-500 sm:block">⌘ K</kbd></div></section>

      <section className="mb-6 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <Kpi label="Loan observations" value={compact.format(meta.counts.positions ?? 0)} detail={`${compact.format(meta.counts.filings ?? 0)} quarterly and annual filings`} icon={Database} />
        <Kpi label="Searchable issuers" value={compact.format(meta.searchableIssuers ?? 0)} detail={`${compact.format(meta.counts.bdcs ?? 0)} reporting BDC vehicles · conservative name gate`} icon={ShieldCheck} />
        <Kpi label="Current median mark" value={fmtMark(meta.currentMedianPrice)} detail={trendDelta == null ? metricLabel : `${trendDelta >= 0 ? "+" : ""}${trendDelta.toFixed(1)} points quarter over quarter`} icon={trendDelta != null && trendDelta < 0 ? ArrowDownRight : ArrowUpRight} />
        <Kpi label="Comparable tranches" value={compact.format(meta.crossHolderMatches)} detail="Two or more holders; confidence scored" icon={FileCheck2} />
      </section>

      <Tabs value={activeTab} onValueChange={setActiveTab} className="gap-0">
        <div className="mb-5 flex flex-wrap items-center justify-between gap-3 border-b border-white/8"><TabsList variant="line" className="h-auto gap-1 overflow-x-auto"><TabsTrigger value="market" className="px-3 pb-3 text-sm">Market</TabsTrigger><TabsTrigger value="dispersion" className="px-3 pb-3 text-sm">Cross-holder dispersion</TabsTrigger><TabsTrigger value="issuer" className="px-3 pb-3 text-sm">Issuer explorer</TabsTrigger><TabsTrigger value="coverage" className="px-3 pb-3 text-sm">Coverage & method</TabsTrigger></TabsList><Select value={metric} onValueChange={value => setMetric(value as Metric)}><SelectTrigger size="sm" className="mb-2 border-white/10 bg-white/[.03] text-slate-200"><SelectValue /></SelectTrigger><SelectContent className="border-white/10 bg-[#0d1725] text-slate-200"><SelectItem value="price">FV / principal</SelectItem><SelectItem value="fvCost">FV / amortized cost</SelectItem></SelectContent></Select></div>

        <TabsContent value="market" className="space-y-4">
          <div className="grid gap-4 xl:grid-cols-[minmax(0,1.45fr)_minmax(360px,.75fr)]">
            <section className="data-panel min-w-0"><PanelTitle title="Loan mark trend" kicker="Median disclosed price" action={<span className="data-chip">{metricLabel}</span>} /><div className="h-[310px] px-2 pb-3 pt-6 sm:px-5"><ResponsiveContainer width="100%" height="100%"><AreaChart data={overview.trend} margin={{ top: 8, right: 10, left: -18, bottom: 0 }}><defs><linearGradient id="markFill" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor="#7dd3fc" stopOpacity={0.26} /><stop offset="100%" stopColor="#7dd3fc" stopOpacity={0} /></linearGradient></defs><CartesianGrid vertical={false} stroke="rgba(148,163,184,.10)" /><XAxis dataKey="quarter" tick={{ fill: "#64748b", fontSize: 11 }} axisLine={false} tickLine={false} minTickGap={26} /><YAxis domain={["dataMin - 2", "dataMax + 2"]} tick={{ fill: "#64748b", fontSize: 11 }} axisLine={false} tickLine={false} /><Tooltip content={<DataTooltip />} /><Area type="monotone" dataKey={metric === "price" ? "medianPrice" : "medianFvCost"} name="Median" stroke="#7dd3fc" strokeWidth={2} fill="url(#markFill)" connectNulls /></AreaChart></ResponsiveContainer></div></section>
            <section className="data-panel min-w-0"><PanelTitle title="Maturity wall" kicker="Latest reported position count" action={<CalendarDays className="size-4 text-slate-500" />} /><div className="h-[310px] px-2 pb-3 pt-6 sm:px-4"><ResponsiveContainer width="100%" height="100%"><BarChart data={overview.maturity} layout="vertical" margin={{ left: 18, right: 14 }}><CartesianGrid horizontal={false} stroke="rgba(148,163,184,.08)" /><XAxis type="number" tickFormatter={value => compact.format(value)} tick={{ fill: "#64748b", fontSize: 10 }} axisLine={false} tickLine={false} /><YAxis type="category" dataKey="bucket" width={105} tick={{ fill: "#94a3b8", fontSize: 11 }} axisLine={false} tickLine={false} /><Tooltip formatter={value => compact.format(Number(value))} cursor={{ fill: "rgba(125,211,252,.04)" }} /><Bar dataKey="positions" name="Positions" fill="#38bdf8" radius={[0, 4, 4, 0]} /></BarChart></ResponsiveContainer></div></section>
          </div>
          <div className="grid gap-4 xl:grid-cols-[minmax(0,1.15fr)_minmax(420px,.85fr)]">
            <section className="data-panel min-w-0"><PanelTitle title={grouping === "holder" ? "BDC scorecard" : "Industry scorecard"} kicker="Latest filing per vehicle" action={<Select value={grouping} onValueChange={value => setGrouping(value as "holder" | "industry")}><SelectTrigger size="sm" className="border-white/10 bg-white/[.03] text-slate-200"><SelectValue /></SelectTrigger><SelectContent className="border-white/10 bg-[#0d1725] text-slate-200"><SelectItem value="holder">By BDC</SelectItem><SelectItem value="industry">By industry</SelectItem></SelectContent></Select>} /><div className="max-h-[430px] overflow-auto scrollbar-thin"><Table><TableHeader className="sticky top-0 z-10 bg-[#0b1522]"><TableRow className="border-white/8 hover:bg-transparent"><TableHead>{grouping === "holder" ? "BDC" : "Industry"}</TableHead><TableHead className="text-right">Observations</TableHead><TableHead className="text-right">Wtd mark</TableHead><TableHead className="text-right">&lt;90</TableHead><TableHead className="text-right">PIK</TableHead></TableRow></TableHeader><TableBody>{groupedRows.slice(0, 40).map(row => { const value = metric === "price" ? row.weightedPrice : row.weightedFvCost; const label = grouping === "holder" ? row.holder : row.industry; return <TableRow key={label} className="border-white/6 hover:bg-sky-300/[.035]"><TableCell className="max-w-[260px] truncate font-medium text-slate-200">{label}</TableCell><TableCell className="text-right font-mono text-xs text-slate-400">{compact.format(row.positions)}</TableCell><TableCell className={`text-right font-mono text-xs ${markTone(value)}`}>{fmtMark(value)}</TableCell><TableCell className="text-right font-mono text-xs text-slate-400">{fmtMark(row.below90Share)}%</TableCell><TableCell className="text-right font-mono text-xs text-slate-400">{fmtMark(row.pikShare)}%</TableCell></TableRow>; })}</TableBody></Table></div></section>
            <section className="data-panel min-w-0"><PanelTitle title="Lowest disclosed marks" kicker="Latest filing per vehicle" action={<span className="data-chip">Review queue</span>} /><div className="divide-y divide-white/6">{overview.riskPositions.slice(0, 8).map(position => { const value = metricValue(position, metric); return <button key={position.id} className="flex w-full items-center gap-3 px-5 py-3 text-left transition hover:bg-white/[.025]" onClick={() => setSelectedIssuer(searchItems.find(item => item.id === position.issuerId) ?? null)}><div className={`w-12 shrink-0 font-mono text-sm font-semibold ${markTone(value)}`}>{fmtMark(value)}</div><div className="min-w-0 flex-1"><div className="truncate text-sm font-medium text-slate-200">{position.issuer}</div><div className="mt-0.5 truncate text-xs text-slate-500">{position.holder} · {position.instrument}</div></div><ChevronRight className="size-4 text-slate-600" /></button>; })}</div></section>
          </div>
        </TabsContent>

        <TabsContent value="dispersion"><section className="data-panel min-w-0"><PanelTitle title="Same instrument, different marks" kicker="Quarter-aligned cross-holder comparison" action={<span className="data-chip">Confidence scored</span>} /><Table><TableHeader><TableRow className="border-white/8 hover:bg-transparent"><TableHead>Issuer / instrument</TableHead><TableHead>Quarter</TableHead><TableHead className="text-right">Holders</TableHead><TableHead className="text-right">Low</TableHead><TableHead className="text-right">High</TableHead><TableHead className="text-right">Range</TableHead><TableHead className="text-right">Confidence</TableHead></TableRow></TableHeader><TableBody>{overview.divergence.map(row => { const low = row.holders.at(0)?.mark, high = row.holders.at(-1)?.mark; return <TableRow key={row.instrumentId} className="cursor-pointer border-white/6 hover:bg-sky-300/[.04]" onClick={() => setSelectedDivergence(row)}><TableCell className="max-w-[520px]"><div className="truncate font-medium text-slate-100">{row.issuer}</div><div className="mt-1 truncate text-xs text-slate-500">{row.instrument}{row.maturity ? ` · due ${row.maturity}` : ""}</div></TableCell><TableCell className="font-mono text-xs text-slate-400">{row.quarter}</TableCell><TableCell className="text-right font-mono text-xs text-slate-400">{row.holders.length}</TableCell><TableCell className={`text-right font-mono text-xs ${markTone(low)}`}>{fmtMark(low)}</TableCell><TableCell className={`text-right font-mono text-xs ${markTone(high)}`}>{fmtMark(high)}</TableCell><TableCell className="text-right font-mono text-xs font-semibold text-amber-300">{row.range.toFixed(1)} pts</TableCell><TableCell className="text-right"><Badge variant="outline" className="border-white/10 bg-white/[.025] font-mono text-[10px] text-slate-400">{Math.round(row.matchConfidence * 100)}%</Badge></TableCell></TableRow>; })}</TableBody></Table></section></TabsContent>

        <TabsContent value="issuer" className="space-y-4">{!selectedIssuer ? <section className="data-panel grid min-h-[420px] place-items-center p-8 text-center"><div className="max-w-md"><Search className="mx-auto mb-4 size-6 text-sky-300" /><h2 className="text-lg font-semibold text-white">Choose an issuer above</h2><p className="mt-2 text-sm leading-6 text-slate-400">The issuer view loads every observed quarter and shows each BDC&apos;s disclosed mark on the same axis.</p></div></section> : <>
          <section className="data-panel"><PanelTitle title={selectedIssuer.name} kicker={selectedIssuer.industry ?? "Unclassified"} action={<span className="data-chip">{selectedIssuer.holders} holder{selectedIssuer.holders === 1 ? "" : "s"}</span>} /><div className="h-[340px] px-2 pb-3 pt-6 sm:px-5"><ResponsiveContainer width="100%" height="100%"><LineChart data={issuerChart} margin={{ top: 8, right: 12, left: -18, bottom: 0 }}><CartesianGrid vertical={false} stroke="rgba(148,163,184,.10)" /><XAxis dataKey="quarter" tick={{ fill: "#64748b", fontSize: 11 }} axisLine={false} tickLine={false} /><YAxis domain={["dataMin - 3", "dataMax + 3"]} tick={{ fill: "#64748b", fontSize: 11 }} axisLine={false} tickLine={false} /><Tooltip content={<DataTooltip />} /><Legend wrapperStyle={{ fontSize: 11, color: "#94a3b8" }} />{issuerHolders.map((holder, index) => <Line key={holder} type="monotone" dataKey={holder} stroke={colors[index]} strokeWidth={1.75} dot={{ r: 2 }} connectNulls />)}</LineChart></ResponsiveContainer></div></section>
          <section className="data-panel min-w-0"><PanelTitle title="Disclosed positions" kicker={`${issuerPositions.length} loan observations`} action={<span className="data-chip">{metricLabel}</span>} /><div className="max-h-[520px] overflow-auto scrollbar-thin"><Table><TableHeader className="sticky top-0 z-10 bg-[#0b1522]"><TableRow className="border-white/8 hover:bg-transparent"><TableHead>Date</TableHead><TableHead>Holder</TableHead><TableHead>Instrument</TableHead><TableHead>Maturity</TableHead><TableHead className="text-right">Principal*</TableHead><TableHead className="text-right">Fair value*</TableHead><TableHead className="text-right">Mark</TableHead><TableHead className="text-right">PIK</TableHead><TableHead>Source</TableHead></TableRow></TableHeader><TableBody>{[...issuerPositions].sort((a,b) => b.date.localeCompare(a.date)).map(position => { const value = metricValue(position, metric); return <TableRow key={position.id} className="border-white/6 hover:bg-sky-300/[.035]"><TableCell className="font-mono text-xs text-slate-400">{position.date}</TableCell><TableCell className="max-w-[240px] truncate text-slate-200">{position.holder}</TableCell><TableCell className="max-w-[300px] truncate text-slate-400">{position.instrument}</TableCell><TableCell className="font-mono text-xs text-slate-400">{position.maturity ?? "—"}</TableCell><TableCell className="text-right font-mono text-xs text-slate-400">{position.principal == null ? "—" : compact.format(position.principal)}</TableCell><TableCell className="text-right font-mono text-xs text-slate-400">{compact.format(position.fairValue)}</TableCell><TableCell className={`text-right font-mono text-xs font-semibold ${markTone(value)}`}>{fmtMark(value)}</TableCell><TableCell className="text-right font-mono text-xs text-slate-400">{position.pikBps == null ? "—" : `${Math.round(position.pikBps)}bp`}</TableCell><TableCell><a href={position.filingUrl} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-xs text-sky-300 hover:text-sky-200">EDGAR <ExternalLink className="size-3" /></a></TableCell></TableRow>; })}</TableBody></Table></div></section>
        </>}</TabsContent>

        <TabsContent value="coverage" className="space-y-4"><div className="grid gap-4 lg:grid-cols-3"><section className="method-card"><Database className="size-5 text-sky-300" /><h2>Structured spine</h2><p>SEC BDC XBRL packages are used from August 2022 forward. Values remain linked to the exact filing.</p></section><section className="method-card"><BookOpen className="size-5 text-amber-300" /><h2>Legacy backfill target</h2><p>2018–mid-2022 filing tables require registrant-specific parsing and are not included in this first published snapshot.</p></section><section className="method-card"><ShieldCheck className="size-5 text-emerald-300" /><h2>Matching guardrail</h2><p>Issuer, maturity, capital-structure position, and spread are combined. Ambiguous matches stay out of the default dispersion view.</p></section></div>
          <section className="data-panel min-w-0"><PanelTitle title="Source packages" kicker="Provenance ledger" action={<a href="https://www.sec.gov/data-research/sec-markets-data/bdc-data-sets" target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-xs text-sky-300">SEC dataset <ExternalLink className="size-3" /></a>} /><Table><TableHeader><TableRow className="border-white/8 hover:bg-transparent"><TableHead>Package</TableHead><TableHead>Coverage</TableHead><TableHead className="text-right">Loan rows</TableHead><TableHead className="text-right">Warnings</TableHead><TableHead>Method</TableHead></TableRow></TableHeader><TableBody>{meta.packages.map(row => <TableRow key={row.package_name} className="border-white/6 hover:bg-sky-300/[.035]"><TableCell><a href={row.source_url} className="inline-flex items-center gap-1 font-mono text-xs text-sky-300" target="_blank" rel="noreferrer">{row.package_name}<ExternalLink className="size-3" /></a></TableCell><TableCell className="font-mono text-xs text-slate-400">{row.period_start ?? "—"} → {row.period_end ?? "—"}</TableCell><TableCell className="text-right font-mono text-xs text-slate-300">{compact.format(row.row_count)}</TableCell><TableCell className="text-right font-mono text-xs text-slate-400">{compact.format(row.warning_count)}</TableCell><TableCell className="text-xs text-slate-400">{row.source_type}</TableCell></TableRow>)}</TableBody></Table></section>
          <div className="rounded-xl border border-amber-300/15 bg-amber-300/[.035] px-5 py-4 text-xs leading-5 text-slate-400"><strong className="text-amber-200">Interpretation:</strong> “Price” is fair value divided by disclosed principal. FV / cost is a second metric, not a substitute. *Principal and fair value are shown in each filer&apos;s as-filed units, so compare ratios—not raw amounts—across BDCs. Holder comparisons are quarter-aligned; differences can still reflect reporting dates, currency, revolver utilization, or amendments. Open the EDGAR source before treating an outlier as an accounting disagreement.</div>
        </TabsContent>
      </Tabs>
    </div>

    <Sheet open={Boolean(selectedDivergence)} onOpenChange={open => !open && setSelectedDivergence(null)}><SheetContent className="w-full border-white/10 bg-[#0a1421] text-slate-200 sm:max-w-xl">{selectedDivergence ? <><SheetHeader className="border-b border-white/8 p-5 pr-12"><SheetTitle className="text-lg text-white">{selectedDivergence.issuer}</SheetTitle><SheetDescription className="leading-5 text-slate-400">{selectedDivergence.instrument}{selectedDivergence.maturity ? ` · due ${selectedDivergence.maturity}` : ""}</SheetDescription></SheetHeader><div className="flex-1 overflow-y-auto px-5 pb-8"><div className="my-5 grid grid-cols-3 gap-2"><div className="mini-stat"><span>Latest range</span><strong>{selectedDivergence.range.toFixed(1)} pts</strong></div><div className="mini-stat"><span>Median mark</span><strong>{fmtMark(selectedDivergence.median)}</strong></div><div className="mini-stat"><span>Confidence</span><strong>{Math.round(selectedDivergence.matchConfidence * 100)}%</strong></div></div><div className="h-52"><ResponsiveContainer width="100%" height="100%"><LineChart data={selectedDivergence.history} margin={{ top: 8, right: 8, left: -24, bottom: 0 }}><CartesianGrid vertical={false} stroke="rgba(148,163,184,.10)" /><XAxis dataKey="quarter" tick={{ fill: "#64748b", fontSize: 10 }} axisLine={false} tickLine={false} /><YAxis tick={{ fill: "#64748b", fontSize: 10 }} axisLine={false} tickLine={false} /><Tooltip content={<DataTooltip />} /><Line type="monotone" dataKey="range" name="Range" stroke="#fbbf24" strokeWidth={2} /></LineChart></ResponsiveContainer></div><h3 className="mb-2 mt-6 text-xs font-semibold uppercase tracking-[0.16em] text-slate-500">Latest holder marks</h3><div className="divide-y divide-white/6 rounded-xl border border-white/8">{selectedDivergence.holders.map(holder => <div key={holder.cik} className="flex items-center gap-4 px-4 py-3"><span className={`w-14 font-mono text-sm font-semibold ${markTone(holder.mark)}`}>{fmtMark(holder.mark)}</span><div className="min-w-0 flex-1"><div className="truncate text-sm text-slate-200">{holder.holder}</div><div className="mt-0.5 text-xs text-slate-500">{compact.format(holder.fairValue)} as-filed FV · based on {holder.basis}</div></div><a href={holder.filingUrl} target="_blank" rel="noreferrer" aria-label={`Open ${holder.holder} filing`} className="text-sky-300"><ExternalLink className="size-4" /></a></div>)}</div></div></> : null}</SheetContent></Sheet>
  </main>;
}
