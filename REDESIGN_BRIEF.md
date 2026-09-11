# Paytm Revenue Recovery Agent — UI/UX Redesign Brief

## Project Overview

A **Paytm-native AI revenue recovery dashboard** for merchants. It shows recovery rates, case management, agent analytics, and A/B test results (treatment vs control vs naive retry). Built as a hackathon concept — not an official Paytm product.

**Tech stack:** FastAPI backend, SQLite/Postgres, React JSX (vendored Babel in-browser, no build step), Chart.js for graphs, GSAP for animations, WebGL for ambient visuals. **Air-gapped** — no CDN, all vendor files in `app/static/vendor/`.

**Files to redesign:**
- `app/static/dashboard.css` — 708 lines, all styling
- `app/static/dashboard.jsx` — 1999 lines, all React components
- `app/static/dashboard.html` — 39 lines, shell
- `app/static/vendor/atmosphere.js` — WebGL plasma + cursor ribbons

---

## Paytm Brand Identity

### Colors (PAYTM_NATIVE palette — use these)
```
Paytm Navy:     #002970  (primary brand, headers, primary actions)
Paytm Cyan:     #00BAF2  (links, interactive, secondary actions)
Paytm Crimson:  #E42352  (errors, urgency, alerts, badges)
Paytm Light:    #7FDBFF  (highlights, active states)
Paytm White:    #FFFFFF  (text on dark, cards on light)
Paytm Grey:     #F5F7FA  (backgrounds, separators)
Paytm Dark:     #1A1A2E  (deep backgrounds)
```

### Typography
- **Primary:** `Inter` or `DM Sans` — clean, modern, Paytm-appropriate
- **Monospace:** `JetBrains Mono` or `Space Mono` — for numbers, code, amounts
- **Scale:** Use a modular scale (12/14/16/20/24/32/40px)
- **Weight:** 400 body, 500 medium, 600 semibold, 700 bold

### Design Language
- **Cards:** Rounded corners (12-16px), subtle shadows, white backgrounds on light theme
- **Buttons:** Rounded (8-12px), navy primary, cyan secondary, crimson destructive
- **Inputs:** Clean borders, focus rings in cyan
- **Spacing:** 8px grid system (8/16/24/32/48/64px)
- **Shadows:** Layered — `0 1px 3px rgba(0,0,0,0.08), 0 4px 12px rgba(0,0,0,0.04)`
- **Transitions:** `200ms ease-out` for interactions, `300ms` for page transitions

---

## Current Component Inventory

### Layout
- **Sidebar** — Logo, nav sections (Core, Intelligence, Operations), footer
- **Top Bar** — KPI strip, theme toggle, tenant selector, notifications, sound, auto-pilot, refresh, run batch
- **Main Content** — Tab-based routing (9 tabs)

### Tabs (9 total)
1. **Hub** — Hero (A/B comparison bars), metrics grid (4 cards), funnel chart, approval rows
2. **Case Ledger** — Searchable table of recovery cases
3. **Engine & ROI** — Bandit stats, CUSUM drift, budget, incidents, SSE events
4. **Analytics** — Summary charts, per-class breakdown
5. **Tools** — Tool schemas and execution
6. **Security** — Audit log, security status
7. **Agent Control** — Squad status, approval queue
8. **Reflection** — Self-reflection logs
9. **Learning** — Cross-session patterns

### Components
- `Tour` — Guided walkthrough with spotlight
- `SeedOverlay` — Loading animation during batch run
- `CommandPalette` — Cmd+K search/navigation
- `LiveTicker` — Real-time event feed
- `AuditModal` — Case detail view
- `HubTab` — Main dashboard with hero, metrics, funnel
- `LedgerTab` — Case table with search/filter
- `EngineTab` — Technical engine metrics
- `AnalyticsTab` — Charts and breakdowns
- `ToolsTab` — Tool management
- `SecurityTab` — Security overview
- `AgentControlTab` — Agent management
- `ReflectionTab` — AI reflection logs
- `LearningTab` — Learning patterns
- `NetworkHealthCard` — Network status
- `BenchmarkCard` — Performance benchmarks
- `PortfolioPanel` — Portfolio overview
- `HeatmapCard` — Usage heatmap

---

## Current CSS Architecture

### Design Tokens (current)
```css
:root {
  --color-bg: oklch(11.8% .008 263);      /* Dark background */
  --color-p1: oklch(15.6% .0095 263);     /* Card background */
  --color-p2: oklch(19.4% .011 263);      /* Elevated surface */
  --color-p3: oklch(24% .012 263);        /* Highest surface */
  --color-line: oklch(100% 0 0/.065);     /* Borders */
  --color-t1: oklch(97% .004 263);        /* Primary text */
  --color-t2: oklch(80% .008 263);        /* Secondary text */
  --color-t3: oklch(68% .01 263);         /* Tertiary text */
  --color-t4: oklch(62% .011 263);        /* Muted text */
  --color-read: oklch(78% .105 233);      /* Steel blue accent */
  --color-ran: oklch(80% .125 172);       /* Teal accent */
  --color-human: oklch(76% .145 302);     /* Purple accent */
  --color-critical: oklch(69% .205 25);   /* Red */
  --color-high: oklch(77% .155 52);       /* Orange */
  --color-medium: oklch(85% .115 86);     /* Green */
  --color-low: oklch(70% .032 250);       /* Grey */
}
```

### Current Problems
1. **Too dark** — Heavy navy/black theme feels like a developer tool, not a fintech product
2. **Too many OKLCH tokens** — Hard to maintain, doesn't match Paytm's clean aesthetic
3. **Font choice** — Archivo is too industrial; Paytm uses cleaner sans-serifs
4. **No light theme** — Currently dark-only (light theme was just added but needs work)
5. **Sidebar too heavy** — Solid dark sidebar feels disconnected from content
6. **Cards too subtle** — Low contrast between card and background
7. **No Paytm brand presence** — Colors and typography don't feel like Paytm
8. **Ambient effects** — WebGL plasma and cursor ribbons feel out of place for a business tool

---

## Redesign Goals

### 1. Paytm-Native Feel
- Use Paytm color palette as primary design language
- Typography should feel like Paytm's apps (clean, modern, readable)
- Card-based layout with clear hierarchy
- Professional but friendly — not developer-tools dark

### 2. Light Theme First
- Primary theme should be light (white backgrounds, dark text)
- Dark theme as alternative (not primary)
- Smooth transitions between themes

### 3. Clean Hierarchy
- **Header:** Paytm navy with logo, clean navigation
- **Cards:** White with subtle shadows, clear section headers
- **Metrics:** Large numbers, clean labels, color-coded status
- **Actions:** Navy primary buttons, cyan secondary, crimson destructive

### 4. Professional Dashboard Feel
- Remove ambient effects (plasma, cursor ribbons) — they distract from data
- Clean data visualization with Paytm colors
- Responsive grid layout
- Clear visual hierarchy — hero metrics → supporting data → details

### 5. Mobile-First Responsive
- Sidebar collapses to hamburger on mobile
- Cards stack vertically on small screens
- Touch-friendly buttons (min 44px tap targets)
- Readable without zoom

---

## Specific Redesign Tasks

### A. Color System
```css
/* Replace current tokens with Paytm-native */
:root {
  /* Paytm brand */
  --paytm-navy: #002970;
  --paytm-cyan: #00BAF2;
  --paytm-crimson: #E42352;
  --paytm-light: #7FDBFF;
  
  /* Neutrals */
  --grey-50: #F9FAFB;
  --grey-100: #F3F4F6;
  --grey-200: #E5E7EB;
  --grey-300: #D1D5DB;
  --grey-400: #9CA3AF;
  --grey-500: #6B7280;
  --grey-600: #4B5563;
  --grey-700: #374151;
  --grey-800: #1F2937;
  --grey-900: #111827;
  
  /* Semantic */
  --success: #10B981;
  --warning: #F59E0B;
  --error: #EF4444;
  --info: #3B82F6;
}
```

### B. Typography
```css
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');

:root {
  --font-sans: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
  --font-mono: 'JetBrains Mono', monospace;
}
```

### C. Component Styles

#### Sidebar
```css
.sidebar {
  background: var(--grey-50);
  border-right: 1px solid var(--grey-200);
  width: 260px;
}
.sidebar-logo h1 {
  color: var(--paytm-navy);
  font-weight: 700;
}
.nav-item {
  border-radius: 8px;
  margin: 2px 8px;
}
.nav-item.active {
  background: var(--paytm-cyan);
  color: white;
}
```

#### Cards
```css
.card {
  background: white;
  border: 1px solid var(--grey-200);
  border-radius: 12px;
  box-shadow: 0 1px 3px rgba(0,0,0,0.08);
}
.card:hover {
  box-shadow: 0 4px 12px rgba(0,0,0,0.08);
}
```

#### Buttons
```css
.btn {
  background: var(--paytm-navy);
  color: white;
  border-radius: 8px;
  font-weight: 500;
}
.btn-secondary {
  background: var(--paytm-cyan);
}
.btn-danger {
  background: var(--paytm-crimson);
}
```

#### Metrics
```css
.metric {
  background: white;
  border: 1px solid var(--grey-200);
  border-radius: 12px;
  padding: 20px;
}
.metric-value {
  font-size: 28px;
  font-weight: 700;
  color: var(--grey-900);
}
.metric-label {
  font-size: 13px;
  color: var(--grey-500);
}
```

### D. Layout Changes
1. **Sidebar:** Light grey background, clean nav items, Paytm logo prominent
2. **Top Bar:** Clean white background, minimal controls, clear hierarchy
3. **Content:** White background cards, consistent spacing, clear sections
4. **Hero:** Clean comparison bars with Paytm colors, not dark/golden

### E. Remove/Replace
- **Remove:** WebGL plasma shader, cursor ribbons, film grain, dark-only theme
- **Replace:** Industrial fonts (Archivo) with clean fonts (Inter)
- **Replace:** OKLCH color system with standard hex colors
- **Replace:** Heavy shadows with subtle, layered shadows

### F. Keep/Enhance
- **Keep:** Tab-based navigation (works well)
- **Keep:** Command palette (Cmd+K) — useful feature
- **Keep:** Guided tour — helpful for onboarding
- **Enhance:** Make tour feel more Paytm-native
- **Enhance:** Make command palette match new design

---

## Reference Designs

### Paytm Dashboard (Internal)
- Clean white backgrounds
- Navy headers
- Cyan accents for interactive elements
- Card-based layout with clear hierarchy
- Professional data visualization

### Stripe Dashboard
- Clean, minimal design
- Clear typography hierarchy
- Subtle shadows and borders
- Professional but friendly feel

### Razorpay Dashboard
- Indian fintech aesthetic
- Clean data presentation
- Professional color scheme
- Mobile-responsive

---

## Implementation Notes

### Files to Modify
1. **`app/static/dashboard.css`** — Complete rewrite with Paytm-native tokens
2. **`app/static/dashboard.jsx`** — Update component styles and classes
3. **`app/static/dashboard.html`** — Update meta tags, remove old scripts
4. **`app/static/vendor/atmosphere.js`** — Remove or replace with subtle effects

### Constraints
- **Air-gapped** — No CDN, all assets local
- **No build step** — JSX compiles in browser with Babel
- **Vendored React** — Keep existing React setup
- **Chart.js** — Keep for data visualization
- **GSAP** — Keep for animations (but use subtly)

### Testing
- Run `pytest tests/ -q --tb=short` — must pass 160+ tests
- Visual testing: Check light and dark themes
- Responsive: Test on mobile viewports
- Accessibility: Ensure proper contrast ratios

---

## Success Criteria

1. **Feels like Paytm** — Colors, typography, and layout match Paytm's design language
2. **Professional** — Clean, business-appropriate dashboard
3. **Light theme first** — Primary experience is light, dark is alternative
4. **Mobile responsive** — Works well on all screen sizes
5. **Accessible** — Proper contrast, keyboard navigation, screen reader support
6. **Fast** — No unnecessary effects that slow down the UI
7. **Maintainable** — Clear CSS architecture, easy to update

---

## Quick Start for Claude

1. Read all 4 files (`dashboard.css`, `dashboard.jsx`, `dashboard.html`, `atmosphere.js`)
2. Understand the current component structure
3. Redesign CSS with Paytm-native tokens
4. Update JSX components to use new classes
5. Test both themes (light and dark)
6. Run tests to ensure nothing breaks
7. Verify responsive design

**Focus on:** Clean, professional, Paytm-native aesthetic. Remove developer-tool feel. Make it look like a real fintech product.
