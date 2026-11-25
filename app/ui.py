DASHBOARD_HTML = """<!DOCTYPE html>
<html class="dark" lang="en">
<head>
  <meta charset="utf-8" />
  <meta content="width=device-width, initial-scale=1.0" name="viewport" />
  <title>PatchPilot — Workspace Vault &amp; Billing</title>
  <link rel="icon" type="image/svg+xml" href="/favicon.svg" />
  <link rel="alternate icon" type="image/x-icon" href="/favicon.ico" />
  <link rel="apple-touch-icon" href="/apple-touch-icon.png" />

  <!-- Fonts -->
  <link rel="preconnect" href="https://fonts.googleapis.com" />
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
  <link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@500;600;700;800&family=Inter:wght@400;500;600&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet" />
  <link href="https://fonts.googleapis.com/css2?family=Material+Symbols+Outlined:wght,FILL@100..700,0..1&display=swap" rel="stylesheet" />

  <!-- Tailwind CSS -->
  <script src="https://cdn.tailwindcss.com"></script>
  <script>
    tailwind.config = {
      darkMode: 'class',
      theme: {
        extend: {
          colors: {
            brand: {
              400: '#fb923c',
              500: '#f97316',
              600: '#ea580c',
            },
            dark: {
              950: '#07090e',
              900: '#0c1017',
              850: '#111722',
              800: '#171f2e',
            }
          },
          fontFamily: {
            sans: ['Inter', 'sans-serif'],
            display: ['Plus Jakarta Sans', 'sans-serif'],
            mono: ['JetBrains Mono', 'monospace'],
          }
        }
      }
    }
  </script>

  <style>
    .material-symbols-outlined {
      font-variation-settings: 'FILL' 0, 'wght' 400, 'GRAD' 0, 'opsz' 20;
      font-size: 17px;
      line-height: 1;
      display: inline-block;
      vertical-align: middle;
    }
    .subtle-grid {
      background-size: 32px 32px;
      background-image: radial-gradient(circle, rgba(255, 255, 255, 0.05) 1px, transparent 1px);
    }
  </style>
</head>
<body class="bg-dark-950 text-slate-100 font-sans antialiased subtle-grid min-h-screen selection:bg-brand-500 selection:text-white flex flex-col">

  <!-- Floating Toast Notification Container -->
  <div id="toastContainer" class="fixed bottom-5 right-5 z-50 flex flex-col gap-2 pointer-events-none"></div>

  <!-- Navbar -->
  <header class="w-full border-b border-white/5 bg-dark-950/85 backdrop-blur-md sticky top-0 z-40">
    <div class="w-full px-6 lg:px-10 h-16 flex items-center justify-between gap-4">
      <div class="flex items-center gap-3">
        <a href="/" class="flex items-center gap-2 group">
          <div class="w-7 h-7 rounded-lg bg-gradient-to-tr from-brand-600 to-amber-500 flex items-center justify-center text-white shadow-sm shadow-brand-500/20 group-hover:scale-105 transition-transform">
            <span class="material-symbols-outlined text-sm">flight_takeoff</span>
          </div>
          <span class="font-display font-bold text-sm tracking-tight text-white">PatchPilot</span>
        </a>
        <span class="text-slate-600 text-xs">/</span>
        <span class="text-xs text-brand-400 font-mono font-medium">Workspace</span>
      </div>

      <nav class="hidden sm:flex items-center gap-6 text-xs font-medium text-slate-400">
        <a href="/" class="hover:text-white transition">Home</a>
        <a href="/pricing" class="hover:text-white transition">Pricing</a>
        <a href="/docs" class="hover:text-white transition flex items-center gap-1">
          <span class="material-symbols-outlined text-sm text-brand-400">menu_book</span>
          <span>Docs &amp; Commands</span>
        </a>
      </nav>

      <div class="flex items-center gap-3">
        <div id="authHeaderBlock"></div>
      </div>
    </div>
  </header>

  <main class="w-full flex-1 px-6 lg:px-10 py-8 flex flex-col gap-6 max-w-7xl mx-auto">

    <!-- Sign In Prompt (when unauthenticated) -->
    <div id="loginCallout" class="hidden w-full rounded-2xl bg-dark-900 border border-white/10 p-6 flex flex-wrap items-center justify-between gap-4">
      <div class="flex items-center gap-3 text-xs sm:text-sm text-slate-300">
        <div class="w-9 h-9 rounded-xl bg-brand-500/10 border border-brand-500/20 flex items-center justify-center text-brand-400 shrink-0">
          <span class="material-symbols-outlined text-base">vpn_key</span>
        </div>
        <div>
          <span class="font-display font-bold text-white">Sign in to manage your GitHub Workspaces, Vault, and Billing</span>
          <p class="text-xs text-slate-400 mt-0.5">Authenticate with Clerk (GitHub OAuth linked) to access personal repositories and GitHub Organizations.</p>
        </div>
      </div>
      <div class="flex items-center gap-2.5">
        <button type="button" onclick="handleClerkAuth()" class="px-4 py-2 rounded-xl bg-brand-500 hover:bg-brand-600 text-white font-display text-xs font-semibold shadow-md shadow-brand-500/25 transition flex items-center gap-1.5 active:scale-95">
          <span class="material-symbols-outlined text-sm">lock</span>
          <span>Sign In</span>
        </button>
        <a id="installAppCta" href="https://github.com/settings/installations" target="_blank" rel="noopener noreferrer" class="px-3.5 py-2 rounded-xl bg-white/5 hover:bg-white/10 border border-white/10 text-slate-300 hover:text-white font-display text-xs font-medium transition flex items-center gap-1.5 active:scale-95">
          <span class="material-symbols-outlined text-sm text-brand-400">add_moderator</span>
          <span>Install GitHub App</span>
        </a>
      </div>
    </div>

    <!-- Authenticated Account + Workspace Switcher Bar -->
    <div id="workspaceBar" class="hidden w-full rounded-2xl bg-dark-900 border border-white/10 p-4 sm:p-5 flex flex-wrap items-center justify-between gap-4">
      <div class="flex flex-wrap items-center gap-4">
        <div class="flex items-center gap-3">
          <img id="userAvatar" src="" class="w-9 h-9 rounded-full border border-white/10 object-cover hidden" alt="Avatar" />
          <div>
            <div class="flex items-center gap-2">
              <span id="userName" class="font-display font-bold text-white text-sm"></span>
              <span id="authBadge" class="text-[10px] font-mono px-2 py-0.5 rounded bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">Verified</span>
            </div>
            <span id="userLogin" class="text-xs font-mono text-slate-400"></span>
          </div>
        </div>

        <div class="h-8 w-px bg-white/10 hidden md:block"></div>

        <!-- Workspace Selector -->
        <div class="flex flex-wrap items-center gap-2">
          <label for="workspaceSelect" class="text-[11px] font-mono text-slate-400 uppercase tracking-wider">Workspace</label>
          <select id="workspaceSelect" onchange="onWorkspaceSelected()"
            class="bg-dark-950 border border-white/15 rounded-xl px-3 py-1.5 text-xs font-mono text-white focus:outline-none focus:border-brand-500 min-w-[200px]">
            <option value="">Loading workspaces...</option>
          </select>
          <span id="wsRoleBadge" class="text-[10px] font-mono px-2 py-0.5 rounded bg-white/5 text-slate-300 border border-white/10"></span>
          <span id="wsPlanBadge" class="text-[10px] font-mono px-2 py-0.5 rounded bg-white/5 text-slate-300 border border-white/10"></span>
        </div>
      </div>

      <div class="flex flex-wrap items-center gap-2">
        <button id="connectGithubBtn" type="button" onclick="connectGitHubAccount()" class="hidden px-3 py-1.5 rounded-lg bg-amber-500/15 hover:bg-amber-500/25 text-amber-300 border border-amber-500/30 text-xs font-medium transition flex items-center gap-1">
          <span class="material-symbols-outlined text-xs">link</span>
          <span>Link GitHub</span>
        </button>
        <button type="button" onclick="syncWorkspaces()" class="px-3 py-1.5 rounded-lg bg-white/5 hover:bg-white/10 text-slate-200 border border-white/10 text-xs font-medium transition flex items-center gap-1">
          <span class="material-symbols-outlined text-xs text-brand-400">sync</span>
          <span>Sync Orgs</span>
        </button>
        <a id="installMoreLink" href="https://github.com/settings/installations" target="_blank" rel="noopener noreferrer" class="px-3 py-1.5 rounded-lg bg-white/5 hover:bg-white/10 text-slate-200 border border-white/10 text-xs font-medium transition flex items-center gap-1">
          <span class="material-symbols-outlined text-xs text-brand-400">add_moderator</span>
          <span>Install on Org</span>
        </a>
        <button type="button" onclick="signOutUser()" class="px-3 py-1.5 rounded-lg bg-white/5 hover:bg-red-500/15 text-slate-300 hover:text-red-300 border border-white/10 text-xs font-medium transition flex items-center gap-1">
          <span class="material-symbols-outlined text-xs">logout</span>
          <span>Sign Out</span>
        </button>
      </div>
    </div>

    <!-- Scoped Repositories Strip -->
    <div id="reposBanner" class="hidden w-full rounded-xl bg-dark-900/70 border border-white/5 px-4 py-3 flex flex-wrap items-center justify-between gap-2 text-xs">
      <div class="flex items-center gap-2 flex-wrap">
        <span class="text-slate-400 font-mono text-[11px]">Protected Repositories:</span>
        <div id="repoPills" class="flex flex-wrap items-center gap-1.5"></div>
      </div>
      <span id="readOnlyNotice" class="hidden text-[11px] font-mono text-amber-400">Read-only member view (GitHub Org admin required to edit settings or billing)</span>
    </div>

    <!-- Subscription & Per-Active-Committer Billing Section -->
    <section id="billingPanel" class="hidden w-full rounded-2xl bg-dark-900 border border-white/10 p-6 space-y-5">
      <div class="flex flex-wrap items-start justify-between gap-4 border-b border-white/5 pb-4">
        <div>
          <div class="flex items-center gap-2">
            <span class="material-symbols-outlined text-brand-400">credit_card</span>
            <h2 class="text-base font-display font-bold text-white tracking-tight">Subscription &amp; Active Committers</h2>
            <span id="billingStatusPill" class="text-[10px] font-mono px-2.5 py-0.5 rounded-full bg-white/5 text-slate-300 border border-white/10">Checking...</span>
          </div>
          <p id="billingSummaryText" class="text-xs text-slate-400 mt-1">
            Pay only for unique human developers who open PRs each billing period. Both Managed AI and BYOK Vault are included in Pro.
          </p>
        </div>

        <div id="billingActions" class="flex flex-wrap items-center gap-2">
          <button id="upgradeBtn" type="button" onclick="startCheckout()" class="px-4 py-2 rounded-xl bg-brand-500 hover:bg-brand-600 text-white font-display text-xs font-semibold shadow-md shadow-brand-500/25 transition flex items-center gap-1.5 active:scale-95">
            <span class="material-symbols-outlined text-sm">bolt</span>
            <span>Subscribe to Pro</span>
          </button>
          <button id="portalBtn" type="button" onclick="openBillingPortal()" class="hidden px-4 py-2 rounded-xl bg-white/5 hover:bg-white/10 text-slate-200 border border-white/10 font-display text-xs font-semibold transition flex items-center gap-1.5 active:scale-95">
            <span class="material-symbols-outlined text-sm">receipt_long</span>
            <span>Manage Billing &amp; Invoices</span>
          </button>
        </div>
      </div>

      <!-- Billing Stats & Controls -->
      <div class="grid grid-cols-1 md:grid-cols-3 gap-4">
        <div class="rounded-xl bg-dark-950 border border-white/5 p-4 space-y-1">
          <div class="text-[11px] font-mono text-slate-400 uppercase">Active Committers (Cycle)</div>
          <div id="statActiveCommitters" class="text-2xl font-display font-bold text-white">0</div>
          <div id="statBillingPeriod" class="text-[11px] font-mono text-slate-500">Period: --</div>
        </div>

        <div class="rounded-xl bg-dark-950 border border-white/5 p-4 space-y-2">
          <div class="text-[11px] font-mono text-slate-400 uppercase">AI Execution Mode</div>
          <div class="grid grid-cols-2 gap-1.5 p-1 rounded-lg bg-dark-900 border border-white/5 text-xs">
            <button id="modeManagedBtn" type="button" onclick="setKeyMode('managed')" class="py-1.5 rounded-md text-slate-400 hover:text-white font-medium transition">Managed AI</button>
            <button id="modeByokBtn" type="button" onclick="setKeyMode('byok')" class="py-1.5 rounded-md bg-brand-500 text-white font-semibold transition">BYOK Vault</button>
          </div>
          <div id="keyModeHint" class="text-[11px] text-slate-500">Choose zero-config Managed AI or your own encrypted API keys.</div>
        </div>

        <div class="rounded-xl bg-dark-950 border border-white/5 p-4 space-y-2">
          <div class="text-[11px] font-mono text-slate-400 uppercase">Optional Monthly Seat Cap</div>
          <div class="flex items-center gap-2">
            <input id="seatCapInput" type="number" min="1" placeholder="Unlimited"
              class="w-full bg-dark-900 border border-white/10 rounded-lg px-3 py-1.5 text-xs font-mono text-white placeholder-slate-600 focus:outline-none focus:border-brand-500" />
            <button type="button" onclick="saveSeatCap()" class="px-3 py-1.5 rounded-lg bg-white/5 hover:bg-white/10 border border-white/10 text-xs font-medium text-white transition shrink-0">
              Save
            </button>
          </div>
          <div class="text-[11px] text-slate-500">Stop adding new billable committers once cap is reached.</div>
        </div>
      </div>

      <!-- Active Committers Table -->
      <div class="overflow-x-auto rounded-xl border border-white/5">
        <table class="w-full text-left border-collapse text-xs">
          <thead>
            <tr class="border-b border-white/5 bg-dark-950/80 text-[11px] font-mono text-slate-400 uppercase tracking-wider">
              <th class="py-2.5 px-3.5">GitHub Developer</th>
              <th class="py-2.5 px-3.5">Reviews This Cycle</th>
              <th class="py-2.5 px-3.5">Managed AI Reviews</th>
              <th class="py-2.5 px-3.5">Meter Sync</th>
              <th class="py-2.5 px-3.5 text-right">Last Active</th>
            </tr>
          </thead>
          <tbody id="activeCommittersBody" class="divide-y divide-white/5 font-mono">
            <tr>
              <td colspan="5" class="py-5 text-center text-xs text-slate-500">
                No active committers recorded in this billing cycle yet.
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <!-- Main 2-Column Vault & Telemetry Grid -->
    <div class="w-full grid grid-cols-1 lg:grid-cols-12 gap-8 items-start">

      <!-- Left Column: Primary BYOK Configuration (lg:col-span-7) -->
      <div class="lg:col-span-7 rounded-2xl bg-dark-900 border border-white/10 p-6 sm:p-8 space-y-6">
        <div class="flex flex-wrap items-center justify-between gap-3 border-b border-white/5 pb-4">
          <div>
            <h1 class="text-lg font-display font-bold text-white tracking-tight">BYOK Encryption Vault</h1>
            <p class="text-xs text-slate-400 mt-0.5">Credentials are encrypted with AES-256-GCM and decrypted in RAM only.</p>
          </div>
          <span id="keyStatusBadge" class="text-xs font-mono px-3 py-1 rounded-full bg-white/5 text-slate-400 border border-white/10 flex items-center gap-1.5">
            <span class="w-1.5 h-1.5 rounded-full bg-slate-500"></span>
            <span>Sign In Required</span>
          </span>
        </div>

        <div id="managedModeBanner" class="hidden rounded-xl border border-emerald-500/25 bg-emerald-500/10 p-3.5 text-xs text-emerald-200 flex items-start gap-2.5">
          <span class="material-symbols-outlined text-emerald-400 text-base shrink-0">auto_awesome</span>
          <div>
            <div class="font-semibold text-white">Managed AI Mode Active</div>
            <p class="text-[11px] text-emerald-200/80 mt-0.5">
              This workspace uses PatchPilot's hosted LLM pool automatically (no API key required). Your Custom Review Policy below still applies to every PR review. Switch to BYOK Vault above if you prefer your own keys.
            </p>
          </div>
        </div>

        <form id="vaultForm" onsubmit="event.preventDefault(); saveVault();" class="space-y-5">
          <!-- LLM Provider Pill Strip -->
          <div>
            <label class="block text-xs font-medium text-slate-300 mb-2">LLM Provider</label>
            <div class="grid grid-cols-3 sm:grid-cols-6 gap-1.5 p-1 rounded-xl bg-dark-950 border border-white/5 text-xs font-medium">
              <button type="button" onclick="selectProvider('gemini')" id="providerBtn-gemini" class="provider-btn py-2 rounded-lg bg-brand-500 text-white font-semibold transition text-center">Gemini</button>
              <button type="button" onclick="selectProvider('openrouter')" id="providerBtn-openrouter" class="provider-btn py-2 rounded-lg text-slate-400 hover:text-white transition text-center">Claude</button>
              <button type="button" onclick="selectProvider('openai')" id="providerBtn-openai" class="provider-btn py-2 rounded-lg text-slate-400 hover:text-white transition text-center">OpenAI</button>
              <button type="button" onclick="selectProvider('groq')" id="providerBtn-groq" class="provider-btn py-2 rounded-lg text-slate-400 hover:text-white transition text-center">Groq</button>
              <button type="button" onclick="selectProvider('grok')" id="providerBtn-grok" class="provider-btn py-2 rounded-lg text-slate-400 hover:text-white transition text-center">xAI</button>
              <button type="button" onclick="selectProvider('ollama')" id="providerBtn-ollama" class="provider-btn py-2 rounded-lg text-slate-400 hover:text-white transition text-center">Ollama</button>
            </div>
            <select id="provider" onchange="onProviderChange()" class="hidden">
              <option value="gemini">Google Gemini</option>
              <option value="openrouter">OpenRouter</option>
              <option value="openai">OpenAI</option>
              <option value="groq">Groq Cloud</option>
              <option value="grok">xAI Grok</option>
              <option value="ollama">Ollama Local</option>
              <option value="custom">Custom Endpoint</option>
            </select>
          </div>

          <!-- Model Identifier -->
          <div>
            <div class="flex items-center justify-between mb-1.5">
              <label class="text-xs font-medium text-slate-300">Model Identifier (Editable)</label>
              <div class="flex items-center gap-1.5 text-[11px] font-mono">
                <button type="button" onclick="setPresetModel('gemini-2.5-flash')" class="text-slate-500 hover:text-brand-400 transition">gemini-2.5-flash</button>
                <span class="text-slate-700">&bull;</span>
                <button type="button" onclick="setPresetModel('anthropic/claude-3.7-sonnet')" class="text-slate-500 hover:text-brand-400 transition">claude-3.7</button>
                <span class="text-slate-700">&bull;</span>
                <button type="button" onclick="setPresetModel('llama-3.3-70b-versatile')" class="text-slate-500 hover:text-brand-400 transition">llama-3.3-70b</button>
                <span class="text-slate-700">&bull;</span>
                <button type="button" onclick="setPresetModel('gpt-4o')" class="text-slate-500 hover:text-brand-400 transition">gpt-4o</button>
              </div>
            </div>
            <input id="model" type="text" list="modelSuggestions" placeholder="e.g. gemini-2.5-pro"
              class="w-full bg-dark-950 border border-white/10 rounded-xl px-3.5 py-2.5 text-xs font-mono text-white placeholder-slate-600 focus:outline-none focus:border-brand-500 transition" />
            <datalist id="modelSuggestions"></datalist>
          </div>

          <!-- API Key -->
          <div>
            <div class="flex items-center justify-between mb-1.5">
              <label class="text-xs font-medium text-slate-300">
                API Key <span id="keyHint" class="text-brand-400 font-mono text-[11px]">(AES-256-GCM Encrypted)</span>
              </label>
            </div>
            <div class="relative flex items-center">
              <input id="apiKey" type="password" placeholder="Paste provider API key (leave empty for Managed AI mode)"
                onfocus="if (this.value === this.dataset.savedMasked) this.select()"
                class="w-full bg-dark-950 border border-white/10 rounded-xl pl-3.5 pr-10 py-2.5 text-xs font-mono text-white placeholder-slate-600 focus:outline-none focus:border-brand-500 transition" />
              <button type="button" onclick="toggleVisibility('apiKey', 'apiKeyEyeIcon')" class="absolute right-3 text-slate-500 hover:text-slate-300 transition" title="Toggle visibility">
                <span id="apiKeyEyeIcon" class="material-symbols-outlined text-base">visibility</span>
              </button>
            </div>
          </div>

          <!-- Base URL & Custom Review Policy -->
          <details class="group rounded-xl border border-white/5 bg-dark-950/60 p-4 transition" open>
            <summary class="flex items-center justify-between cursor-pointer text-xs font-medium text-slate-400 hover:text-white select-none">
              <span class="flex items-center gap-1.5">
                <span class="material-symbols-outlined text-sm">tune</span>
                <span>Custom Review Policy &amp; Endpoint Override</span>
              </span>
              <span class="material-symbols-outlined text-sm text-slate-500 group-open:rotate-180 transition-transform">expand_more</span>
            </summary>

            <div class="pt-4 space-y-4 border-t border-white/5 mt-3">
              <div>
                <label class="block text-[11px] font-medium text-slate-400 mb-1">Base URL Override (Optional)</label>
                <input id="baseUrl" type="text" placeholder="https://api.example.com/v1"
                  class="w-full bg-dark-950 border border-white/10 rounded-lg px-3 py-2 text-xs font-mono text-white focus:outline-none focus:border-brand-500" />
              </div>

              <div>
                <div class="flex items-center justify-between mb-1">
                  <label class="text-[11px] font-medium text-slate-400">Custom Review Policy (Markdown)</label>
                  <div class="flex items-center gap-1.5 text-[10px]">
                    <button type="button" onclick="applyPolicyTemplate('security')" class="text-brand-400 hover:underline">Security</button>
                    <span class="text-slate-600">&bull;</span>
                    <button type="button" onclick="applyPolicyTemplate('perf')" class="text-brand-400 hover:underline">Perf</button>
                    <span class="text-slate-600">&bull;</span>
                    <button type="button" onclick="applyPolicyTemplate('strict')" class="text-brand-400 hover:underline">Strict</button>
                  </div>
                </div>
                <textarea id="reviewPolicy" rows="4"
                  class="w-full bg-dark-950 border border-white/10 rounded-lg p-3 text-xs font-mono text-slate-300 focus:outline-none focus:border-brand-500 leading-relaxed resize-none"
                  placeholder="Optional custom review guidelines..."></textarea>
              </div>
            </div>
          </details>

          <div id="alertBox" class="hidden"></div>

          <!-- Action Controls -->
          <div class="flex items-center justify-between gap-3 pt-2">
            <button type="button" onclick="loadActiveWorkspace()"
              class="text-xs text-slate-500 hover:text-slate-300 transition flex items-center gap-1">
              <span class="material-symbols-outlined text-sm">sync</span>
              <span>Reload Config</span>
            </button>

            <div class="flex items-center gap-2">
              <button id="testPrimaryBtn" type="button" onclick="testConnection('primary')"
                class="px-4 py-2 rounded-xl bg-dark-850 hover:bg-dark-800 text-slate-300 border border-white/10 text-xs font-medium transition flex items-center gap-1.5 active:scale-95">
                <span class="material-symbols-outlined text-amber-400 text-sm">bolt</span>
                <span>Test Primary</span>
              </button>
              <button id="saveVaultBtn" type="submit"
                class="px-5 py-2 rounded-xl bg-brand-500 hover:bg-brand-600 text-white font-display text-xs font-semibold shadow-md shadow-brand-500/20 transition flex items-center gap-1.5 active:scale-95">
                <span>Save Credentials</span>
              </button>
            </div>
          </div>
        </form>
      </div>

      <!-- Right Column: Telemetry & Interactive Commands (lg:col-span-5) -->
      <div class="lg:col-span-5 space-y-6">
        <div class="rounded-2xl bg-dark-900 border border-white/10 p-5 space-y-3">
          <div class="flex items-center justify-between border-b border-white/5 pb-2.5">
            <div class="flex items-center gap-2">
              <span class="material-symbols-outlined text-brand-400">terminal</span>
              <h2 class="font-display font-bold text-white text-xs sm:text-sm">Connection Telemetry</h2>
            </div>
            <span class="text-[10px] font-mono px-2 py-0.5 rounded bg-white/5 text-slate-400 border border-white/5">TLS 1.3</span>
          </div>

          <div id="testConsole" class="rounded-xl bg-dark-950 border border-white/5 p-3.5 font-mono text-xs space-y-1.5 min-h-[110px] text-slate-400 overflow-y-auto">
            <p class="text-slate-600">// Click "Test Primary" to probe your configured BYOK provider.</p>
            <p class="text-slate-600">// Validates latency and key authorization in real time.</p>
          </div>
        </div>

        <div class="rounded-2xl bg-dark-900 border border-white/10 p-5 space-y-3">
          <div class="flex items-center justify-between border-b border-white/5 pb-2.5">
            <div class="flex items-center gap-2">
              <span class="material-symbols-outlined text-brand-400">chat</span>
              <h3 class="font-display font-bold text-white text-xs sm:text-sm">Interactive PR Commands</h3>
            </div>
            <a href="/docs" class="text-xs text-brand-400 hover:underline">Full Docs &rarr;</a>
          </div>

          <div class="space-y-2 text-xs">
            <div class="p-2.5 rounded-xl bg-dark-950 border border-white/5 flex items-center justify-between gap-2">
              <div>
                <code class="text-brand-400 font-mono font-semibold">@PatchPilot review</code>
                <p class="text-[11px] text-slate-400">Triggers complete 2-pass verified PR audit</p>
              </div>
              <button onclick="copyCommand('@PatchPilot review')" class="px-2 py-1 rounded bg-white/5 hover:bg-white/10 text-slate-400 hover:text-white text-[10px] font-mono transition">Copy</button>
            </div>
            <div class="p-2.5 rounded-xl bg-dark-950 border border-white/5 flex items-center justify-between gap-2">
              <div>
                <code class="text-brand-400 font-mono font-semibold">@PatchPilot explain</code>
                <p class="text-[11px] text-slate-400">Plain-English architectural breakdown</p>
              </div>
              <button onclick="copyCommand('@PatchPilot explain')" class="px-2 py-1 rounded bg-white/5 hover:bg-white/10 text-slate-400 hover:text-white text-[10px] font-mono transition">Copy</button>
            </div>
            <div class="p-2.5 rounded-xl bg-dark-950 border border-white/5 flex items-center justify-between gap-2">
              <div>
                <code class="text-brand-400 font-mono font-semibold">@PatchPilot fix</code>
                <p class="text-[11px] text-slate-400">Generates 1-click committable suggestion</p>
              </div>
              <button onclick="copyCommand('@PatchPilot fix')" class="px-2 py-1 rounded bg-white/5 hover:bg-white/10 text-slate-400 hover:text-white text-[10px] font-mono transition">Copy</button>
            </div>
            <div class="p-2.5 rounded-xl bg-dark-950 border border-white/5 flex items-center justify-between gap-2">
              <div>
                <code class="text-brand-400 font-mono font-semibold">@PatchPilot test</code>
                <p class="text-[11px] text-slate-400">Synthesizes edge-case unit tests</p>
              </div>
              <button onclick="copyCommand('@PatchPilot test')" class="px-2 py-1 rounded bg-white/5 hover:bg-white/10 text-slate-400 hover:text-white text-[10px] font-mono transition">Copy</button>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- Saved Credentials Inventory & Sequential Multi-Provider Fallback Chain -->
    <div class="w-full rounded-2xl bg-dark-900 border border-white/10 p-6 sm:p-8 space-y-4">
      <div class="flex flex-wrap items-center justify-between gap-3 border-b border-white/5 pb-3">
        <div>
          <div class="flex items-center gap-2">
            <span class="material-symbols-outlined text-brand-400">shield_lock</span>
            <h2 class="text-sm font-display font-bold text-white tracking-tight">Saved BYOK Keys &amp; Sequential Fallback Chain</h2>
          </div>
          <p class="text-xs text-slate-400 mt-0.5">Automated multi-key failover when primary encounters 429, 401/402/403, 5xx outages, or timeouts.</p>
        </div>
        <button id="addFallbackBtn" type="button" onclick="openAddFallbackModal()"
          class="px-3.5 py-2 rounded-xl bg-brand-500 hover:bg-brand-600 text-white text-xs font-semibold shadow-md shadow-brand-500/20 transition flex items-center gap-1.5 active:scale-95">
          <span class="material-symbols-outlined text-sm">add</span>
          <span>Add Fallback Key</span>
        </button>
      </div>

      <div class="overflow-x-auto rounded-xl border border-white/5">
        <table class="w-full text-left border-collapse text-xs">
          <thead>
            <tr class="border-b border-white/5 bg-dark-950/80 text-[11px] font-mono text-slate-400 uppercase tracking-wider">
              <th class="py-2.5 px-3.5">Name / Label</th>
              <th class="py-2.5 px-3.5">Provider &amp; Model</th>
              <th class="py-2.5 px-3.5">Key Preview</th>
              <th class="py-2.5 px-3.5">Role</th>
              <th class="py-2.5 px-3.5 text-right">Actions</th>
            </tr>
          </thead>
          <tbody id="savedKeysTableContainer" class="divide-y divide-white/5 font-mono">
            <tr>
              <td colspan="5" class="py-6 text-center text-xs text-slate-500">
                Sign in and select a workspace to view saved credentials.
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <!-- Add Fallback Key Modal -->
    <div id="fallbackModal" class="hidden fixed inset-0 z-50 bg-black/80 backdrop-blur-sm flex items-center justify-center p-4">
      <div class="bg-dark-900 border border-white/10 rounded-2xl p-6 max-w-lg w-full shadow-2xl space-y-4">
        <div class="flex items-center justify-between border-b border-white/5 pb-3">
          <div class="flex items-center gap-2">
            <span class="material-symbols-outlined text-brand-400">add_moderator</span>
            <h3 class="text-sm font-display font-bold text-white">Add Secondary Fallback Credential</h3>
          </div>
          <button onclick="closeAddFallbackModal()" class="text-slate-500 hover:text-white text-lg leading-none">&times;</button>
        </div>

        <form onsubmit="event.preventDefault(); submitFallback();" class="space-y-3.5">
          <div>
            <label class="block text-[11px] font-medium text-slate-400 mb-1">Fallback Label</label>
            <input id="fbName" type="text" placeholder="e.g. Groq Backup or OpenRouter"
              class="w-full bg-dark-950 border border-white/10 rounded-xl px-3.5 py-2 text-xs font-mono text-white placeholder-slate-600 focus:outline-none focus:border-brand-500" />
          </div>

          <div class="grid grid-cols-2 gap-3">
            <div>
              <label class="block text-[11px] font-medium text-slate-400 mb-1">Provider</label>
              <select id="fbProvider" onchange="onFallbackProviderChange()"
                class="w-full bg-dark-950 border border-white/10 rounded-xl px-3 py-2 text-xs font-mono text-white focus:outline-none focus:border-brand-500">
                <option value="groq">Groq Cloud</option>
                <option value="gemini">Google Gemini</option>
                <option value="openrouter">OpenRouter</option>
                <option value="openai">OpenAI</option>
                <option value="ollama">Ollama (Local)</option>
                <option value="custom">Custom Endpoint</option>
              </select>
            </div>
            <div>
              <label class="block text-[11px] font-medium text-slate-400 mb-1">Model Slug (Editable)</label>
              <input id="fbModel" type="text" value="llama-3.3-70b-versatile"
                class="w-full bg-dark-950 border border-white/10 rounded-xl px-3.5 py-2 text-xs font-mono text-white focus:outline-none focus:border-brand-500" />
            </div>
          </div>

          <div>
            <label class="block text-[11px] font-medium text-slate-400 mb-1">API Key</label>
            <div class="relative flex items-center">
              <input id="fbApiKey" type="password" placeholder="Paste fallback API key"
                class="w-full bg-dark-950 border border-white/10 rounded-xl pl-3.5 pr-10 py-2 text-xs font-mono text-white placeholder-slate-600 focus:outline-none focus:border-brand-500" />
              <button type="button" onclick="toggleVisibility('fbApiKey', 'fbApiKeyEyeIcon')" class="absolute right-3 text-slate-500 hover:text-slate-300">
                <span id="fbApiKeyEyeIcon" class="material-symbols-outlined text-sm">visibility</span>
              </button>
            </div>
          </div>

          <div>
            <label class="block text-[11px] font-medium text-slate-400 mb-1">Base URL (Optional)</label>
            <input id="fbBaseUrl" type="text" placeholder="https://api.groq.com/openai/v1"
              class="w-full bg-dark-950 border border-white/10 rounded-xl px-3.5 py-2 text-xs font-mono text-white placeholder-slate-600 focus:outline-none focus:border-brand-500" />
          </div>

          <div class="flex items-center justify-end gap-2 pt-2 border-t border-white/5">
            <button type="button" onclick="closeAddFallbackModal()"
              class="px-4 py-2 rounded-xl bg-white/5 hover:bg-white/10 text-slate-300 text-xs font-medium transition">Cancel</button>
            <button type="submit"
              class="px-5 py-2 rounded-xl bg-brand-500 hover:bg-brand-600 text-white font-display text-xs font-semibold shadow-md shadow-brand-500/20 transition active:scale-95">Save Fallback Key</button>
          </div>
        </form>
      </div>
    </div>
  </main>

  <footer class="border-t border-white/5 py-6 text-center text-xs text-slate-500 font-mono mt-auto">
    <div class="max-w-7xl mx-auto px-6 flex flex-col sm:flex-row items-center justify-between gap-4">
      <div>
        <span class="font-display font-bold text-slate-300">PatchPilot</span> &bull;
        <span>Managed &amp; BYOK AI Pull Request Reviewer</span>
      </div>
      <div class="flex items-center gap-4 text-slate-400">
        <a href="/pricing" class="hover:text-white transition">Pricing</a>
        <a href="/privacy" class="hover:text-white transition">Privacy Policy</a>
        <a href="/terms" class="hover:text-white transition">Terms of Service</a>
        <a href="/docs" class="hover:text-white transition">Docs</a>
      </div>
    </div>
  </footer>

  <script>
    const PRESET_MODELS = {
      gemini: ["gemini-2.5-flash", "gemini-2.5-pro"],
      openrouter: ["anthropic/claude-3.7-sonnet", "deepseek/deepseek-chat-v3-0324", "openai/gpt-4o", "x-ai/grok-3"],
      openai: ["gpt-4o", "gpt-4.1", "o3-mini"],
      groq: ["llama-3.3-70b-versatile", "llama-3.1-8b-instant"],
      grok: ["grok-3", "grok-3-mini"],
      ollama: ["qwen2.5-coder:14b", "deepseek-coder-v2:16b", "llama3.1:8b"],
      custom: []
    };

    const POLICY_TEMPLATES = {
      security: `# Security & OWASP Policy
- Enforce parameterized queries; flag raw SQL string interpolations.
- Reject hardcoded secrets, plain tokens, and unmasked credentials.
- Verify explicit CSRF/CORS protection on non-idempotent endpoints.`,
      perf: `# Performance & Efficiency Policy
- Flag O(N^2) loops on request-response hot paths.
- Enforce connection pooling and explicit timeout limits on network I/O.`,
      strict: `# Strict Code Quality Policy
- Require comprehensive type annotations across all exported symbols.
- Flag unhandled exception paths and mandate specific error types.`
    };

    let clerkInstance = null;
    let appConfig = {
      publishable_key: "",
      frontend_api: "",
      accounts_url: "",
      install_url: "https://github.com/settings/installations",
      billing_enabled: false,
      github_oauth_configured: false
    };
    let currentUser = null;
    let workspacesList = [];
    let activeWorkspace = null;

    function escapeHtml(str) {
      if (!str) return "";
      const div = document.createElement("div");
      div.textContent = String(str);
      return div.innerHTML;
    }

    function showToast(msg, type = "info") {
      const container = document.getElementById("toastContainer");
      const toast = document.createElement("div");
      toast.className = `px-4 py-2.5 rounded-xl border text-xs font-mono shadow-2xl pointer-events-auto flex items-center gap-2 transition-all duration-300 transform translate-y-2 opacity-0 ${
        type === "error"
          ? "bg-red-950/95 border-red-800 text-red-200"
          : type === "success"
          ? "bg-dark-900/95 border-emerald-500/40 text-emerald-200"
          : "bg-dark-900/95 border-brand-500/40 text-slate-200"
      }`;
      const icon = type === "error" ? "error" : type === "success" ? "check_circle" : "info";
      const iconColor = type === "error" ? "text-red-400" : type === "success" ? "text-emerald-400" : "text-brand-400";
      toast.innerHTML = `<span class="material-symbols-outlined text-sm ${iconColor}">${icon}</span><span>${escapeHtml(msg)}</span>`;
      container.appendChild(toast);
      requestAnimationFrame(() => toast.classList.remove("translate-y-2", "opacity-0"));
      setTimeout(() => {
        toast.classList.add("opacity-0", "translate-y-2");
        setTimeout(() => toast.remove(), 300);
      }, 3600);
    }

    function copyCommand(cmd) {
      navigator.clipboard.writeText(cmd).then(() => showToast(`Copied: ${cmd}`, "info")).catch(() => showToast(`Copied: ${cmd}`, "info"));
    }

    function applyPolicyTemplate(type) {
      if (POLICY_TEMPLATES[type]) {
        document.getElementById("reviewPolicy").value = POLICY_TEMPLATES[type];
        showToast(`Applied ${type.toUpperCase()} policy template`, "info");
      }
    }

    function toggleVisibility(inputId, iconId) {
      const field = document.getElementById(inputId);
      const icon = document.getElementById(iconId);
      if (field.type === "password") {
        field.type = "text";
        icon.textContent = "visibility_off";
      } else {
        field.type = "password";
        icon.textContent = "visibility";
      }
    }

    function setPresetModel(name) {
      document.getElementById("model").value = name;
    }

    function selectProvider(key) {
      document.getElementById("provider").value = key;
      updateActiveProviderBtn(key);
      onProviderChange();
    }

    function updateActiveProviderBtn(activeKey) {
      document.querySelectorAll(".provider-btn").forEach(btn => {
        btn.className = "provider-btn py-2 rounded-lg text-slate-400 hover:text-white transition text-center";
      });
      const activeBtn = document.getElementById(`providerBtn-${activeKey}`);
      if (activeBtn) {
        activeBtn.className = "provider-btn py-2 rounded-lg bg-brand-500 text-white font-semibold transition text-center";
      }
    }

    function onProviderChange() {
      const provider = document.getElementById("provider").value;
      updateActiveProviderBtn(provider);
      const list = document.getElementById("modelSuggestions");
      list.innerHTML = "";
      (PRESET_MODELS[provider] || []).forEach(m => {
        const opt = document.createElement("option");
        opt.value = m;
        list.appendChild(opt);
      });
      if (PRESET_MODELS[provider]?.length && !document.getElementById("model").value) {
        document.getElementById("model").value = PRESET_MODELS[provider][0];
      }
    }

    async function loadAppConfig() {
      try {
        const res = await fetch("/auth/clerk/config");
        if (res.ok) {
          appConfig = await res.json();
          const installUrl = appConfig.install_url || "https://github.com/settings/installations";
          const cta = document.getElementById("installAppCta");
          const more = document.getElementById("installMoreLink");
          if (cta) cta.href = installUrl;
          if (more) more.href = installUrl;
        }
      } catch (err) {
        console.debug("Config fetch notice:", err);
      }
    }

    async function initClerk() {
      if (!appConfig.publishable_key || !appConfig.frontend_api) return;
      try {
        const domain = appConfig.frontend_api;
        if (!window.Clerk) {
          if (!document.querySelector('script[src*="/npm/@clerk/ui"]')) {
            const s1 = document.createElement("script");
            s1.defer = true;
            s1.crossOrigin = "anonymous";
            s1.src = `https://${domain}/npm/@clerk/ui@1/dist/ui.browser.js`;
            document.head.appendChild(s1);
          }
          if (!document.querySelector('script[src*="/npm/@clerk/clerk-js"]')) {
            await new Promise((resolve) => {
              const s2 = document.createElement("script");
              s2.defer = true;
              s2.crossOrigin = "anonymous";
              s2.setAttribute("data-clerk-publishable-key", appConfig.publishable_key);
              s2.src = `https://${domain}/npm/@clerk/clerk-js@6/dist/clerk.browser.js`;
              s2.onload = resolve;
              s2.onerror = resolve;
              document.head.appendChild(s2);
            });
          }
        }

        let attempts = 0;
        while (!window.Clerk && attempts < 25) {
          await new Promise(r => setTimeout(r, 100));
          attempts++;
        }

        if (window.Clerk) {
          clerkInstance = window.Clerk;
          if (!clerkInstance.loaded) {
            await clerkInstance.load({
              publishableKey: appConfig.publishable_key,
              frontendApi: domain,
            });
          }
          if (clerkInstance.user) {
            const token = await clerkInstance.session.getToken();
            await fetch("/auth/clerk/session", {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ session_token: token })
            });
          }
        }
      } catch (err) {
        console.debug("Clerk init:", err);
      }
    }

    async function handleClerkAuth() {
      const targetUrl = `${window.location.origin}/dashboard`;
      if (clerkInstance && typeof clerkInstance.redirectToSignIn === "function") {
        await clerkInstance.redirectToSignIn({
          signInForceRedirectUrl: targetUrl,
          signUpForceRedirectUrl: targetUrl,
        });
        return;
      }
      if (appConfig.accounts_url) {
        const target = encodeURIComponent(targetUrl);
        window.location.href = `${appConfig.accounts_url}/sign-in?sign_in_force_redirect_url=${target}&sign_up_force_redirect_url=${target}&redirect_url=${target}`;
        return;
      }
      if (appConfig.github_oauth_configured) {
        window.location.href = "/auth/login";
        return;
      }
      showToast("Authentication provider is not configured on this server yet.", "error");
    }

    async function connectGitHubAccount() {
      if (clerkInstance && clerkInstance.user) {
        try {
          showToast("Opening GitHub OAuth connection...", "info");
          const res = await clerkInstance.user.createExternalAccount({
            strategy: "oauth_github",
            redirect_url: window.location.href,
          });
          if (res.verification?.externalVerificationRedirectURL) {
            window.location.href = res.verification.externalVerificationRedirectURL.href;
            return;
          }
        } catch (err) {
          showToast(`GitHub link failed: ${err.message || "Check Clerk GitHub OAuth settings"}`, "error");
          return;
        }
      }
      if (appConfig.github_oauth_configured) {
        window.location.href = "/auth/login";
      } else {
        showToast("Link GitHub from your Clerk account settings to sync repositories.", "info");
      }
    }

    async function signOutUser() {
      if (clerkInstance) {
        try { await clerkInstance.signOut(); } catch (e) {}
      }
      window.location.href = "/auth/logout";
    }

    async function checkAuth() {
      const res = await fetch("/auth/me");
      const data = await res.json();
      const authBlock = document.getElementById("authHeaderBlock");

      if (data.logged_in && data.user) {
        currentUser = data.user;
        document.getElementById("workspaceBar").classList.remove("hidden");
        document.getElementById("loginCallout").classList.add("hidden");

        const avatarEl = document.getElementById("userAvatar");
        if (data.user.avatar_url) {
          avatarEl.src = data.user.avatar_url;
          avatarEl.classList.remove("hidden");
        }
        document.getElementById("userName").textContent = data.user.name || data.user.login;
        document.getElementById("userLogin").textContent = `@${data.user.github_username || data.user.login}`;
        document.getElementById("authBadge").textContent = data.provider === "clerk" ? "Clerk SSO" : "GitHub OAuth";

        if (!data.user.github_connected) {
          document.getElementById("connectGithubBtn").classList.remove("hidden");
        }

        authBlock.innerHTML = `
          <div class="flex items-center gap-2 px-2.5 py-1 rounded-lg bg-white/5 border border-white/10 text-xs">
            <span class="text-white font-mono text-[11px]">@${escapeHtml(data.user.github_username || data.user.login)}</span>
          </div>
        `;
        await loadWorkspaces();
      } else {
        currentUser = null;
        document.getElementById("loginCallout").classList.remove("hidden");
        document.getElementById("workspaceBar").classList.add("hidden");
        document.getElementById("billingPanel").classList.add("hidden");
        document.getElementById("reposBanner").classList.add("hidden");
        authBlock.innerHTML = `
          <button onclick="handleClerkAuth()" class="px-3 py-1.5 rounded-lg bg-brand-500 hover:bg-brand-600 text-white font-semibold text-xs transition flex items-center gap-1.5 shadow-md shadow-brand-500/20 active:scale-95">
            <span class="material-symbols-outlined text-xs">lock</span>
            <span>Sign In</span>
          </button>
        `;
      }
    }

    async function loadWorkspaces() {
      const select = document.getElementById("workspaceSelect");
      const res = await fetch("/api/workspaces");
      if (!res.ok) {
        select.innerHTML = '<option value="">Unable to load workspaces</option>';
        return;
      }
      const data = await res.json();
      workspacesList = data.workspaces || [];

      if (!workspacesList.length) {
        select.innerHTML = '<option value="">No GitHub workspace linked</option>';
        document.getElementById("connectGithubBtn").classList.remove("hidden");
        document.getElementById("billingPanel").classList.add("hidden");
        return;
      }

      const params = new URLSearchParams(window.location.search);
      const requestedWsId = params.get("workspace") || localStorage.getItem("pp_active_workspace") || "";

      select.innerHTML = workspacesList.map(ws => {
        const typeLabel = ws.account_type === "Organization" ? "Org" : "Personal";
        return `<option value="${escapeHtml(ws.id)}">${escapeHtml(ws.github_login)} (${typeLabel})</option>`;
      }).join("");

      const matched = workspacesList.find(w => w.id === requestedWsId) || workspacesList[0];
      select.value = matched.id;
      await onWorkspaceSelected();

      if (params.get("checkout") === "success") {
        showToast("Checkout completed! Syncing subscription state...", "success");
      }
    }

    async function syncWorkspaces() {
      showToast("Syncing GitHub App installations and organization roles...", "info");
      const res = await fetch("/api/workspaces/sync", { method: "POST" });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        showToast(err.detail || "Workspace sync failed", "error");
        return;
      }
      showToast("Workspaces synced from GitHub.", "success");
      await loadWorkspaces();
    }

    async function onWorkspaceSelected() {
      const wsId = document.getElementById("workspaceSelect").value;
      activeWorkspace = workspacesList.find(w => w.id === wsId) || null;
      if (!activeWorkspace) return;

      localStorage.setItem("pp_active_workspace", activeWorkspace.id);
      updateWorkspaceHeaderBadges();
      await loadActiveWorkspace();
    }

    function updateWorkspaceHeaderBadges() {
      if (!activeWorkspace) return;
      const roleEl = document.getElementById("wsRoleBadge");
      const planEl = document.getElementById("wsPlanBadge");
      const readOnlyEl = document.getElementById("readOnlyNotice");

      roleEl.textContent = activeWorkspace.role.toUpperCase();
      if (!activeWorkspace.billing_enabled) {
        planEl.textContent = "SELF-HOSTED";
        planEl.className = "text-[10px] font-mono px-2 py-0.5 rounded bg-emerald-500/10 text-emerald-400 border border-emerald-500/20";
      } else if (activeWorkspace.is_subscribed) {
        planEl.textContent = activeWorkspace.cancel_at_period_end ? "PRO (CANCELING)" : "PRO ACTIVE";
        planEl.className = "text-[10px] font-mono px-2 py-0.5 rounded bg-emerald-500/10 text-emerald-400 border border-emerald-500/20";
      } else {
        planEl.textContent = "SUBSCRIPTION REQUIRED";
        planEl.className = "text-[10px] font-mono px-2 py-0.5 rounded bg-amber-500/15 text-amber-300 border border-amber-500/30";
      }

      const isAdmin = Boolean(activeWorkspace.is_admin);
      readOnlyEl.classList.toggle("hidden", isAdmin);
      document.getElementById("saveVaultBtn").disabled = !isAdmin;
      document.getElementById("testPrimaryBtn").disabled = !isAdmin;
      document.getElementById("addFallbackBtn").classList.toggle("hidden", !isAdmin);
      document.getElementById("billingActions").classList.toggle("hidden", !isAdmin);
    }

    async function loadActiveWorkspace() {
      if (!activeWorkspace) return;
      await Promise.all([
        loadWorkspaceVault(activeWorkspace.id),
        loadWorkspaceBilling(activeWorkspace.id),
        loadWorkspaceRepos(activeWorkspace.id),
      ]);
    }

    async function loadWorkspaceRepos(wsId) {
      const banner = document.getElementById("reposBanner");
      const pills = document.getElementById("repoPills");
      banner.classList.remove("hidden");
      try {
        const res = await fetch(`/api/workspaces/${encodeURIComponent(wsId)}/repos`);
        if (!res.ok) return;
        const data = await res.json();
        const repos = data.repos || [];
        if (!data.installed) {
          pills.innerHTML = `<span class="text-amber-400 font-mono text-[11px]">GitHub App not installed on this account yet.</span>
            <a href="${escapeHtml(data.install_url || appConfig.install_url)}" target="_blank" rel="noopener noreferrer" class="text-brand-400 hover:underline font-mono text-[11px]">Install App</a>`;
          return;
        }
        if (!repos.length) {
          pills.innerHTML = `<span class="text-slate-400 font-mono text-[11px]">Installation active (0 repos listed)</span>`;
          return;
        }
        pills.innerHTML = repos.slice(0, 5).map(r => {
          const safeName = escapeHtml(r.full_name || r.name);
          const vis = r.private ? "private" : "public";
          return `<span class="px-2 py-0.5 rounded bg-white/5 border border-white/10 text-slate-300 text-[10px] font-mono">${safeName} <span class="text-slate-500">(${vis})</span></span>`;
        }).join("") + (repos.length > 5 ? `<span class="text-slate-500 text-[10px] font-mono">+${repos.length - 5} more</span>` : "");
      } catch (err) {
        pills.innerHTML = `<span class="text-slate-500 font-mono text-[11px]">Repository scope unavailable</span>`;
      }
    }

    async function loadWorkspaceBilling(wsId) {
      const panel = document.getElementById("billingPanel");
      const res = await fetch(`/api/workspaces/${encodeURIComponent(wsId)}/billing`);
      if (!res.ok) {
        panel.classList.add("hidden");
        return;
      }
      const data = await res.json();
      const ws = data.workspace;
      activeWorkspace = ws;
      updateWorkspaceHeaderBadges();

      panel.classList.remove("hidden");

      const statusPill = document.getElementById("billingStatusPill");
      const summaryEl = document.getElementById("billingSummaryText");
      const upgradeBtn = document.getElementById("upgradeBtn");
      const portalBtn = document.getElementById("portalBtn");

      if (!ws.billing_enabled) {
        statusPill.textContent = "SELF-HOSTED MODE";
        statusPill.className = "text-[10px] font-mono px-2.5 py-0.5 rounded-full bg-emerald-500/10 text-emerald-400 border border-emerald-500/20";
        summaryEl.textContent = "Billing enforcement is disabled (BILLING_ENABLED=false). All repositories use your BYOK vault.";
        upgradeBtn.classList.add("hidden");
        portalBtn.classList.add("hidden");
      } else if (ws.is_subscribed) {
        statusPill.textContent = ws.cancel_at_period_end ? "PRO (CANCELING AT PERIOD END)" : "PRO ACTIVE";
        statusPill.className = "text-[10px] font-mono px-2.5 py-0.5 rounded-full bg-emerald-500/10 text-emerald-400 border border-emerald-500/20";
        const renewDate = ws.current_period_end ? new Date(ws.current_period_end).toLocaleDateString() : "Active";
        summaryEl.textContent = `Per-active-committer Pro subscription active for ${ws.github_login}. Current cycle ends ${renewDate}.`;
        upgradeBtn.classList.add("hidden");
        portalBtn.classList.remove("hidden");
      } else {
        statusPill.textContent = "SUBSCRIPTION REQUIRED";
        statusPill.className = "text-[10px] font-mono px-2.5 py-0.5 rounded-full bg-amber-500/15 text-amber-300 border border-amber-500/30";
        summaryEl.textContent = `Subscribe ${ws.github_login} to PatchPilot Pro to activate PR reviews. Billed per unique human developer opening PRs each month (Managed AI + BYOK Vault included).`;
        upgradeBtn.classList.remove("hidden");
        portalBtn.classList.add("hidden");
      }

      document.getElementById("statActiveCommitters").textContent = String(ws.active_committers_count || 0);
      document.getElementById("statBillingPeriod").textContent = `Period: ${data.billing_period}`;
      document.getElementById("seatCapInput").value = ws.seat_cap ? String(ws.seat_cap) : "";

      renderKeyModeUI(ws.key_mode || "byok");

      const tbody = document.getElementById("activeCommittersBody");
      const committers = data.active_committers || [];
      if (!committers.length) {
        tbody.innerHTML = '<tr><td colspan="5" class="py-5 text-center text-xs text-slate-500">No active committers recorded in this billing cycle yet.</td></tr>';
      } else {
        tbody.innerHTML = committers.map(c => {
          const syncBadge = c.meter_reported
            ? '<span class="px-2 py-0.5 rounded bg-emerald-500/10 text-emerald-400 border border-emerald-500/20 text-[10px]">Synced</span>'
            : '<span class="px-2 py-0.5 rounded bg-amber-500/10 text-amber-300 border border-amber-500/20 text-[10px]">Pending</span>';
          const lastSeen = c.last_seen_at ? new Date(c.last_seen_at).toLocaleString() : "--";
          return `
            <tr class="hover:bg-white/[0.01]">
              <td class="py-2.5 px-3.5 text-white">@${escapeHtml(c.github_author_login)}</td>
              <td class="py-2.5 px-3.5 text-slate-300">${c.review_count}</td>
              <td class="py-2.5 px-3.5 text-slate-300">${c.managed_review_count}</td>
              <td class="py-2.5 px-3.5">${syncBadge}</td>
              <td class="py-2.5 px-3.5 text-right text-slate-400">${escapeHtml(lastSeen)}</td>
            </tr>
          `;
        }).join("");
      }
    }

    function renderKeyModeUI(mode) {
      const mBtn = document.getElementById("modeManagedBtn");
      const bBtn = document.getElementById("modeByokBtn");
      const banner = document.getElementById("managedModeBanner");
      if (mode === "managed") {
        mBtn.className = "py-1.5 rounded-md bg-brand-500 text-white font-semibold transition";
        bBtn.className = "py-1.5 rounded-md text-slate-400 hover:text-white font-medium transition";
        banner.classList.remove("hidden");
      } else {
        bBtn.className = "py-1.5 rounded-md bg-brand-500 text-white font-semibold transition";
        mBtn.className = "py-1.5 rounded-md text-slate-400 hover:text-white font-medium transition";
        banner.classList.add("hidden");
      }
    }

    async function setKeyMode(mode) {
      if (!activeWorkspace || !activeWorkspace.is_admin) {
        showToast("Only workspace owners/admins can change key mode.", "error");
        return;
      }
      const res = await fetch(`/api/workspaces/${encodeURIComponent(activeWorkspace.id)}/billing/settings`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ key_mode: mode })
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        showToast(err.detail || "Unable to switch key mode", "error");
        return;
      }
      const data = await res.json();
      activeWorkspace = data.workspace;
      renderKeyModeUI(activeWorkspace.key_mode);
      showToast(`Switched execution mode to ${mode.toUpperCase()}.`, "success");
    }

    async function saveSeatCap() {
      if (!activeWorkspace || !activeWorkspace.is_admin) {
        showToast("Only workspace owners/admins can update seat caps.", "error");
        return;
      }
      const rawVal = document.getElementById("seatCapInput").value.trim();
      const body = rawVal === "" ? { clear_seat_cap: true } : { seat_cap: parseInt(rawVal, 10) };
      const res = await fetch(`/api/workspaces/${encodeURIComponent(activeWorkspace.id)}/billing/settings`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body)
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        showToast(err.detail || "Failed to update seat cap", "error");
        return;
      }
      showToast(rawVal ? `Monthly seat cap set to ${rawVal}.` : "Monthly seat cap cleared (unlimited).", "success");
      await loadWorkspaceBilling(activeWorkspace.id);
    }

    async function startCheckout() {
      if (!activeWorkspace) return;
      showToast("Creating Polar checkout session...", "info");
      const res = await fetch(`/api/workspaces/${encodeURIComponent(activeWorkspace.id)}/billing/checkout`, { method: "POST" });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        showToast(err.detail || "Checkout unavailable", "error");
        return;
      }
      const data = await res.json();
      if (data.checkout_url) {
        window.location.href = data.checkout_url;
      }
    }

    async function openBillingPortal() {
      if (!activeWorkspace) return;
      showToast("Opening customer billing portal...", "info");
      const res = await fetch(`/api/workspaces/${encodeURIComponent(activeWorkspace.id)}/billing/portal`, { method: "POST" });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        showToast(err.detail || "Portal unavailable", "error");
        return;
      }
      const data = await res.json();
      if (data.portal_url) {
        window.location.href = data.portal_url;
      }
    }

    async function loadWorkspaceVault(wsId) {
      const res = await fetch(`/api/workspaces/${encodeURIComponent(wsId)}/vault`);
      if (!res.ok) {
        showToast(`Failed to load workspace vault (${res.status})`, "error");
        return;
      }
      const data = await res.json();
      document.getElementById("provider").value = data.provider;
      updateActiveProviderBtn(data.provider);
      document.getElementById("model").value = data.model;
      document.getElementById("baseUrl").value = data.base_url || "";
      document.getElementById("reviewPolicy").value = data.review_policy || "";

      const statusBadge = document.getElementById("keyStatusBadge");
      const keyInput = document.getElementById("apiKey");
      if (data.has_api_key) {
        statusBadge.innerHTML = '<span class="w-1.5 h-1.5 rounded-full bg-emerald-400"></span><span>BYOK Key Active</span>';
        statusBadge.className = "text-xs font-mono px-2.5 py-1 rounded-full bg-emerald-500/10 text-emerald-400 border border-emerald-500/20 flex items-center gap-1.5";
        document.getElementById("keyHint").textContent = "(Saved in Vault)";
        keyInput.value = data.masked_api_key || "";
        keyInput.dataset.savedMasked = data.masked_api_key || "";
      } else {
        statusBadge.innerHTML = '<span class="w-1.5 h-1.5 rounded-full bg-amber-400"></span><span>No BYOK Key</span>';
        statusBadge.className = "text-xs font-mono px-2.5 py-1 rounded-full bg-amber-500/10 text-amber-400 border border-amber-500/20 flex items-center gap-1.5";
        document.getElementById("keyHint").textContent = "(Optional in Managed AI)";
        keyInput.value = "";
        delete keyInput.dataset.savedMasked;
      }
      renderSavedKeysTable(data);
    }

    function renderSavedKeysTable(data) {
      const container = document.getElementById("savedKeysTableContainer");
      const primaryMasked = data.masked_api_key || (data.has_api_key ? "••••••••" : "Not configured");
      const isAdmin = Boolean(activeWorkspace && activeWorkspace.is_admin);
      const rows = [];

      rows.push(`
        <tr class="border-b border-white/5 bg-white/[0.02]">
          <td class="py-3 px-3.5">
            <div class="flex items-center gap-1.5 font-medium text-white">
              <span class="w-2 h-2 rounded-full bg-emerald-400"></span>
              <span>Primary BYOK Provider</span>
            </div>
            <span class="text-[10px] text-slate-500 font-mono">Default BYOK Route</span>
          </td>
          <td class="py-3 px-3.5">
            <div class="text-xs text-white capitalize font-semibold">${escapeHtml(data.provider || 'gemini')}</div>
            <div class="text-[11px] text-slate-400 font-mono">${escapeHtml(data.model || 'gemini-2.5-pro')}</div>
          </td>
          <td class="py-3 px-3.5 font-mono text-xs text-brand-400">${escapeHtml(primaryMasked)}</td>
          <td class="py-3 px-3.5">
            <span class="text-[10px] font-mono px-2 py-0.5 rounded bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">Primary</span>
          </td>
          <td class="py-3 px-3.5 text-right">
            ${isAdmin ? `<button type="button" onclick="testConnection('primary')" class="px-2.5 py-1 rounded-lg bg-dark-850 hover:bg-dark-800 text-slate-300 border border-white/10 text-xs font-medium transition inline-flex items-center gap-1"><span class="material-symbols-outlined text-xs text-amber-400">bolt</span><span>Test</span></button>` : '<span class="text-slate-500 text-[11px]">Read-only</span>'}
          </td>
        </tr>
      `);

      (data.fallbacks || []).forEach((fb, idx) => {
        const masked = fb.masked_api_key || (fb.has_api_key ? "••••••••" : "Not configured");
        rows.push(`
          <tr class="border-b border-white/5 hover:bg-white/[0.01] transition">
            <td class="py-3 px-3.5">
              <div class="flex items-center gap-1.5 font-medium text-slate-200">
                <span class="w-2 h-2 rounded-full bg-brand-400"></span>
                <span>${escapeHtml(fb.name || `Fallback ${idx + 1}`)}</span>
              </div>
              <span class="text-[10px] text-slate-500 font-mono">Priority #${idx + 1}</span>
            </td>
            <td class="py-3 px-3.5">
              <div class="text-xs text-white capitalize font-semibold">${escapeHtml(fb.provider)}</div>
              <div class="text-[11px] text-slate-400 font-mono">${escapeHtml(fb.model)}</div>
            </td>
            <td class="py-3 px-3.5 font-mono text-xs text-brand-400">${escapeHtml(masked)}</td>
            <td class="py-3 px-3.5">
              <span class="text-[10px] font-mono px-2 py-0.5 rounded bg-brand-500/10 text-brand-400 border border-brand-500/20">Fallback</span>
            </td>
            <td class="py-3 px-3.5 text-right flex items-center justify-end gap-1.5">
              ${isAdmin ? `
                <button type="button" onclick="testConnection('${escapeHtml(fb.id)}')" class="px-2 py-1 rounded-lg bg-dark-850 hover:bg-dark-800 text-slate-300 border border-white/10 text-xs font-medium transition" title="Test fallback"><span class="material-symbols-outlined text-xs text-amber-400">bolt</span></button>
                <button type="button" onclick="promoteFallback('${escapeHtml(fb.id)}')" class="px-2.5 py-1 rounded-lg bg-white/5 hover:bg-brand-500 text-slate-300 hover:text-white border border-white/10 text-xs font-medium transition">Make Primary</button>
                <button type="button" onclick="deleteFallback('${escapeHtml(fb.id)}')" class="px-2 py-1 rounded-lg bg-red-500/10 hover:bg-red-500/20 text-red-400 border border-red-500/20 text-xs font-medium transition"><span class="material-symbols-outlined text-xs">delete</span></button>
              ` : '<span class="text-slate-500 text-[11px]">Read-only</span>'}
            </td>
          </tr>
        `);
      });

      container.innerHTML = rows.join("");
    }

    async function saveVault() {
      if (!activeWorkspace || !activeWorkspace.is_admin) {
        showToast("Only workspace owners/admins can save credentials.", "error");
        return;
      }
      const keyInput = document.getElementById("apiKey");
      const currentKey = keyInput.value.trim();
      const apiKeyToSend = (currentKey === keyInput.dataset.savedMasked) ? "" : currentKey;

      const payload = {
        provider: document.getElementById("provider").value,
        model: document.getElementById("model").value.trim(),
        api_key: apiKeyToSend,
        base_url: document.getElementById("baseUrl").value.trim() || null,
        review_policy: document.getElementById("reviewPolicy").value
      };

      const res = await fetch(`/api/workspaces/${encodeURIComponent(activeWorkspace.id)}/vault`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        showToast(`Save failed: ${err.detail || res.status}`, "error");
        return;
      }
      showToast("Vault credentials & policy encrypted with AES-256-GCM and saved.", "success");
      await loadWorkspaceVault(activeWorkspace.id);
    }

    function openAddFallbackModal() {
      document.getElementById("fallbackModal").classList.remove("hidden");
      document.getElementById("fbName").value = "";
      document.getElementById("fbApiKey").value = "";
      document.getElementById("fbBaseUrl").value = "";
      onFallbackProviderChange();
    }

    function closeAddFallbackModal() {
      document.getElementById("fallbackModal").classList.add("hidden");
    }

    function onFallbackProviderChange() {
      const p = document.getElementById("fbProvider").value;
      const defaults = {
        groq: "llama-3.3-70b-versatile",
        gemini: "gemini-2.5-flash",
        openrouter: "anthropic/claude-3.7-sonnet",
        openai: "gpt-4o",
        ollama: "qwen2.5-coder:14b",
        custom: "custom-model"
      };
      document.getElementById("fbModel").value = defaults[p] || "gemini-2.5-flash";
    }

    async function submitFallback() {
      if (!activeWorkspace) return;
      const payload = {
        name: document.getElementById("fbName").value.trim() || undefined,
        provider: document.getElementById("fbProvider").value,
        model: document.getElementById("fbModel").value.trim(),
        api_key: document.getElementById("fbApiKey").value.trim() || null,
        base_url: document.getElementById("fbBaseUrl").value.trim() || null,
        is_enabled: true
      };
      const res = await fetch(`/api/workspaces/${encodeURIComponent(activeWorkspace.id)}/vault/fallbacks`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        showToast(err.detail || "Failed to add fallback", "error");
        return;
      }
      closeAddFallbackModal();
      showToast("Fallback credential added to failover chain.", "success");
      await loadWorkspaceVault(activeWorkspace.id);
    }

    async function promoteFallback(fallbackId) {
      if (!activeWorkspace) return;
      const res = await fetch(`/api/workspaces/${encodeURIComponent(activeWorkspace.id)}/vault/fallbacks/${encodeURIComponent(fallbackId)}/set-primary`, { method: "POST" });
      if (!res.ok) {
        showToast("Failed to promote fallback", "error");
        return;
      }
      showToast("Promoted fallback to Primary provider.", "success");
      await loadWorkspaceVault(activeWorkspace.id);
    }

    async function deleteFallback(fallbackId) {
      if (!activeWorkspace || !confirm("Remove this fallback credential?")) return;
      const res = await fetch(`/api/workspaces/${encodeURIComponent(activeWorkspace.id)}/vault/fallbacks/${encodeURIComponent(fallbackId)}`, { method: "DELETE" });
      if (!res.ok) {
        showToast("Failed to delete fallback", "error");
        return;
      }
      showToast("Fallback credential removed.", "info");
      await loadWorkspaceVault(activeWorkspace.id);
    }

    async function testConnection(fallbackId = null) {
      if (!activeWorkspace) return;
      if (!fallbackId || fallbackId === "primary") {
        await saveVault();
      }
      const consoleBox = document.getElementById("testConsole");
      const startTime = performance.now();
      consoleBox.innerHTML = `<div class="text-brand-400">Probing provider connection...</div>`;
      const query = (fallbackId && fallbackId !== "primary") ? `?fallback_id=${encodeURIComponent(fallbackId)}` : "";
      const res = await fetch(`/api/workspaces/${encodeURIComponent(activeWorkspace.id)}/vault/test${query}`, { method: "POST" });
      const latency = Math.round(performance.now() - startTime);
      const data = await res.json().catch(() => ({}));

      if (!res.ok) {
        showToast(`Probe failed (${latency}ms)`, "error");
        consoleBox.innerHTML = `<div class="text-red-400 font-semibold">Probe Failed (${latency}ms)</div><div class="text-red-300 text-[11px] mt-1">${escapeHtml(data.detail || "Connection error")}</div>`;
        return;
      }
      showToast(`Probe verified in ${latency}ms`, "success");
      consoleBox.innerHTML = `
        <div class="text-emerald-400 font-semibold">Handshake Verified (${latency}ms)</div>
        <div class="text-slate-300 mt-1">Provider: <span class="text-white">${escapeHtml(data.provider)}</span> | Model: <span class="text-white">${escapeHtml(data.model)}</span></div>
        <div class="mt-2 text-[11px] bg-dark-950 border border-white/5 p-2 rounded text-emerald-300">"${escapeHtml(data.reply)}"</div>
      `;
    }

    window.addEventListener("DOMContentLoaded", async () => {
      onProviderChange();
      await loadAppConfig();
      await initClerk();
      await checkAuth();
    });
  </script>
</body>
</html>
"""
