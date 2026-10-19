DASHBOARD_HTML = """<!DOCTYPE html>
<html class="dark" lang="en">
<head>
  <meta charset="utf-8" />
  <meta content="width=device-width, initial-scale=1.0" name="viewport" />
  <title>PatchPilot — BYOK Encryption Vault</title>
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

  <!-- Full-Width Navbar (Clean, No Models Link) -->
  <header class="w-full border-b border-white/5 bg-dark-950/85 backdrop-blur-md sticky top-0 z-40">
    <div class="w-full px-6 lg:px-10 h-16 flex items-center justify-between">
      
      <!-- Brand & Section -->
      <div class="flex items-center gap-3">
        <a href="/" class="flex items-center gap-2 group">
          <div class="w-7 h-7 rounded-lg bg-gradient-to-tr from-brand-600 to-amber-500 flex items-center justify-center text-white shadow-sm shadow-brand-500/20 group-hover:scale-105 transition-transform">
            <span class="material-symbols-outlined text-sm">flight_takeoff</span>
          </div>
          <span class="font-display font-bold text-sm tracking-tight text-white">PatchPilot</span>
        </a>
        <span class="text-slate-600 text-xs">/</span>
        <span class="text-xs text-brand-400 font-mono font-medium">Vault</span>
      </div>

      <!-- Center Links (No Models) -->
      <nav class="hidden sm:flex items-center gap-6 text-xs font-medium text-slate-400">
        <a href="/" class="hover:text-white transition">Home</a>
        <a href="/docs" class="hover:text-white transition flex items-center gap-1">
          <span class="material-symbols-outlined text-sm text-brand-400">menu_book</span>
          <span>Docs &amp; Commands</span>
        </a>
        <a href="https://github.com/89Aman/PatchPilot" target="_blank" rel="noreferrer" class="hover:text-white transition">GitHub</a>
      </nav>

      <!-- Right: Auth -->
      <div class="flex items-center gap-3">
        <div id="authHeaderBlock"></div>
      </div>

    </div>
  </header>

  <!-- Full-Screen Vault Canvas -->
  <main class="w-full flex-1 px-6 lg:px-10 py-8 flex flex-col gap-6">

    <!-- Account & Scope Banner -->
    <div id="identityCard" class="hidden w-full rounded-2xl bg-dark-900 border border-white/10 p-4 sm:p-5 flex flex-wrap items-center justify-between gap-4">
      <div class="flex items-center gap-3">
        <img id="userAvatar" src="" class="w-9 h-9 rounded-full border border-white/10 object-cover" alt="Avatar" />
        <div>
          <div class="flex items-center gap-2">
            <span id="userName" class="font-display font-bold text-white text-sm"></span>
            <span id="authBadge" class="text-[10px] font-mono px-2 py-0.5 rounded bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">Authenticated</span>
          </div>
          <span id="userLogin" class="text-xs font-mono text-slate-400"></span>
        </div>
      </div>
      <div class="flex items-center gap-3">
        <div id="repoPills" class="flex flex-wrap items-center gap-1.5"></div>
        <button onclick="loadUserRepos()" class="text-xs text-brand-400 hover:text-brand-300 font-medium flex items-center gap-1 transition" title="Refresh repositories">
          <span class="material-symbols-outlined text-sm">refresh</span>
          <span>Refresh</span>
        </button>
        <button onclick="signOutUser()" class="text-xs px-3 py-1.5 rounded-lg bg-white/5 hover:bg-white/10 text-slate-300 border border-white/5 transition flex items-center gap-1">
          <span class="material-symbols-outlined text-xs">logout</span>
          <span>Sign Out</span>
        </button>
      </div>
    </div>

    <!-- Sign In Prompt (if unauthenticated) -->
    <div id="loginCallout" class="hidden w-full rounded-2xl bg-dark-900 border border-white/10 p-4 sm:p-5 flex flex-wrap items-center justify-between gap-4">
      <div class="flex items-center gap-3 text-xs sm:text-sm text-slate-300">
        <div class="w-8 h-8 rounded-lg bg-white/5 border border-white/10 flex items-center justify-center text-slate-300 shrink-0">
          <span class="material-symbols-outlined text-base">vpn_key</span>
        </div>
        <div>
          <span class="font-medium text-white">Sign in to auto-populate your BYOK vault</span>
          <p class="text-xs text-slate-400">Authenticate with your account for instant identity and repository mapping.</p>
        </div>
      </div>
      <div class="flex items-center gap-2.5">
        <button type="button" onclick="handleClerkAuth()" class="px-4 py-2 rounded-xl bg-brand-500 hover:bg-brand-600 text-white font-display text-xs font-semibold shadow-md shadow-brand-500/25 transition flex items-center gap-1.5 active:scale-95">
          <span class="material-symbols-outlined text-sm">lock</span>
          <span>Sign In</span>
        </button>
        <a href="https://github.com/apps/patchpilot-app/installations/new" target="_blank" rel="noopener noreferrer" class="px-3.5 py-2 rounded-xl bg-white/5 hover:bg-white/10 border border-white/10 text-slate-300 hover:text-white font-display text-xs font-medium transition flex items-center gap-1.5 active:scale-95">
          <span class="material-symbols-outlined text-sm text-brand-400">add_moderator</span>
          <span>Install App &amp; Scope Repos</span>
        </a>
      </div>
    </div>

    <!-- Authentication Modal -->
    <div id="clerkModal" class="hidden fixed inset-0 z-50 bg-black/80 backdrop-blur-sm flex items-center justify-center p-4">
      <div class="bg-dark-900 border border-white/10 rounded-2xl p-6 max-w-md w-full shadow-2xl space-y-4">
        <div class="flex items-center justify-between border-b border-white/5 pb-3">
          <div class="flex items-center gap-2">
            <span class="material-symbols-outlined text-brand-400">lock</span>
            <h3 class="text-sm font-display font-bold text-white">Sign In to PatchPilot</h3>
          </div>
          <button onclick="document.getElementById('clerkModal').classList.add('hidden')" class="text-slate-500 hover:text-white text-lg leading-none">&times;</button>
        </div>
        <p class="text-xs text-slate-300 leading-relaxed">
          Sign in using Google, GitHub, or Email to access your encrypted BYOK Vault.
        </p>
        <div class="flex justify-end gap-2 pt-2">
          <button onclick="document.getElementById('clerkModal').classList.add('hidden')" class="px-4 py-2 rounded-xl bg-white/5 hover:bg-white/10 border border-white/10 text-white text-xs font-medium transition">
            Close
          </button>
          <a href="/auth/login" class="px-4 py-2 rounded-xl bg-brand-500 hover:bg-brand-600 text-white text-xs font-semibold shadow-md shadow-brand-500/25 transition flex items-center gap-1">
            <span>Continue with GitHub</span>
            <span class="material-symbols-outlined text-xs">arrow_forward</span>
          </a>
        </div>
      </div>
    </div>

    <!-- GitHub Token & Repo Scope Modal -->
    <div id="githubTokenModal" class="hidden fixed inset-0 z-50 bg-black/80 backdrop-blur-sm flex items-center justify-center p-4">
      <div class="bg-dark-900 border border-white/10 rounded-2xl p-6 max-w-md w-full shadow-2xl space-y-4">
        <div class="flex items-center justify-between border-b border-white/5 pb-3">
          <div class="flex items-center gap-2">
            <span class="material-symbols-outlined text-brand-400">lock</span>
            <h3 class="text-sm font-display font-bold text-white">Connect GitHub &amp; Allow Repository Scope</h3>
          </div>
          <button onclick="closeTokenModal()" class="text-slate-500 hover:text-white text-lg leading-none">&times;</button>
        </div>
        <p class="text-xs text-slate-300 leading-relaxed">
          Provide a GitHub Personal Access Token (PAT) with <code class="text-brand-400 font-mono">repo</code> and <code class="text-brand-400 font-mono">read:user</code> scopes to inspect pull requests and configure your vault.
        </p>
        <div class="space-y-2">
          <label class="text-[11px] font-mono text-slate-400 block">Personal Access Token (PAT)</label>
          <input id="githubTokenInput" type="password" placeholder="ghp_xxxxxxxxxxxxxxxxxxxx"
            class="w-full bg-dark-950 border border-white/10 rounded-xl px-3.5 py-2.5 text-xs font-mono text-white placeholder-slate-600 focus:outline-none focus:border-brand-500 transition" />
        </div>
        <div class="text-[11px] text-slate-400 space-y-1 bg-dark-950 p-3 rounded-xl border border-white/5 font-mono">
          <div>1. Generate token at <a href="https://github.com/settings/tokens/new?scopes=repo,read:user" target="_blank" class="text-brand-400 hover:underline">github.com/settings/tokens</a></div>
          <div>2. Select <span class="text-emerald-400">repo</span> scope and paste here.</div>
        </div>
        <div class="flex items-center justify-end gap-2 pt-2">
          <button onclick="closeTokenModal()" class="px-4 py-2 rounded-xl bg-white/5 hover:bg-white/10 border border-white/10 text-white text-xs font-medium transition">
            Cancel
          </button>
          <button onclick="submitGitHubToken()" class="px-4 py-2 rounded-xl bg-brand-500 hover:bg-brand-600 text-white text-xs font-semibold shadow-md shadow-brand-500/20 transition flex items-center gap-1.5 active:scale-95">
            <span class="material-symbols-outlined text-xs">check</span>
            <span>Connect &amp; Scope Repos</span>
          </button>
        </div>
      </div>
    </div>

    <!-- Full-Screen 2-Column Grid -->
    <div class="w-full grid grid-cols-1 lg:grid-cols-12 gap-8 items-start">

      <!-- Left Column: Primary BYOK Configuration (lg:col-span-7) -->
      <div class="lg:col-span-7 rounded-2xl bg-dark-900 border border-white/10 p-6 sm:p-8 space-y-6">
        
        <!-- Header -->
        <div class="flex flex-wrap items-center justify-between gap-3 border-b border-white/5 pb-4">
          <div>
            <h1 class="text-lg font-display font-bold text-white tracking-tight">BYOK Encryption Vault</h1>
            <p class="text-xs text-slate-400 mt-0.5">Credentials are encrypted with AES-256-GCM and decrypted in RAM only.</p>
          </div>
          <span id="keyStatusBadge" class="text-xs font-mono px-3 py-1 rounded-full bg-white/5 text-slate-400 border border-white/10 flex items-center gap-1.5">
            <span class="w-1.5 h-1.5 rounded-full bg-slate-500"></span>
            <span>Checking Vault...</span>
          </span>
        </div>

        <form onsubmit="event.preventDefault(); saveVault();" class="space-y-5">
          
          <!-- LLM Provider Pill Strip -->
          <div>
            <label class="block text-xs font-medium text-slate-300 mb-2">LLM Provider</label>
            <div class="grid grid-cols-3 sm:grid-cols-6 gap-1.5 p-1 rounded-xl bg-dark-950 border border-white/5 text-xs font-medium">
              <button type="button" onclick="selectProvider('gemini')" id="providerBtn-gemini"
                class="provider-btn py-2 rounded-lg bg-brand-500 text-white font-semibold transition text-center">Gemini</button>
              <button type="button" onclick="selectProvider('openrouter')" id="providerBtn-openrouter"
                class="provider-btn py-2 rounded-lg text-slate-400 hover:text-white transition text-center">Claude</button>
              <button type="button" onclick="selectProvider('openai')" id="providerBtn-openai"
                class="provider-btn py-2 rounded-lg text-slate-400 hover:text-white transition text-center">OpenAI</button>
              <button type="button" onclick="selectProvider('groq')" id="providerBtn-groq"
                class="provider-btn py-2 rounded-lg text-slate-400 hover:text-white transition text-center">Groq</button>
              <button type="button" onclick="selectProvider('grok')" id="providerBtn-grok"
                class="provider-btn py-2 rounded-lg text-slate-400 hover:text-white transition text-center">xAI</button>
              <button type="button" onclick="selectProvider('ollama')" id="providerBtn-ollama"
                class="provider-btn py-2 rounded-lg text-slate-400 hover:text-white transition text-center">Ollama</button>
            </div>
            <!-- Synchronized select -->
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

          <!-- Model Identifier & Chips -->
          <div>
            <div class="flex items-center justify-between mb-1.5">
              <label class="text-xs font-medium text-slate-300">Model Identifier</label>
              <div class="flex items-center gap-1.5 text-[11px] font-mono">
                <button type="button" onclick="setPresetModel('gemini-2.5-pro')" class="text-slate-500 hover:text-brand-400 transition">gemini-2.5-pro</button>
                <span class="text-slate-700">•</span>
                <button type="button" onclick="setPresetModel('anthropic/claude-3.7-sonnet')" class="text-slate-500 hover:text-brand-400 transition">claude-3.7</button>
                <span class="text-slate-700">•</span>
                <button type="button" onclick="setPresetModel('llama-3.3-70b-versatile')" class="text-slate-500 hover:text-brand-400 transition">llama-3.3-70b</button>
                <span class="text-slate-700">•</span>
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
              <input id="apiKey" type="password" placeholder="Paste API key (e.g. gsk_... or AIza...)"
                onfocus="if (this.value === this.dataset.savedMasked) this.select()"
                class="w-full bg-dark-950 border border-white/10 rounded-xl pl-3.5 pr-10 py-2.5 text-xs font-mono text-white placeholder-slate-600 focus:outline-none focus:border-brand-500 transition" />
              <button type="button" onclick="toggleVisibility('apiKey', 'apiKeyEyeIcon')" class="absolute right-3 text-slate-500 hover:text-slate-300 transition" title="Toggle visibility">
                <span id="apiKeyEyeIcon" class="material-symbols-outlined text-base">visibility</span>
              </button>
            </div>
          </div>

          <!-- Collapsible: Advanced & Policy -->
          <details class="group rounded-xl border border-white/5 bg-dark-950/60 p-4 transition">
            <summary class="flex items-center justify-between cursor-pointer text-xs font-medium text-slate-400 hover:text-white select-none">
              <span class="flex items-center gap-1.5">
                <span class="material-symbols-outlined text-sm">tune</span>
                <span>Advanced Token &amp; Custom Review Policy</span>
              </span>
              <span class="material-symbols-outlined text-sm text-slate-500 group-open:rotate-180 transition-transform">expand_more</span>
            </summary>
            
            <div class="pt-4 space-y-4 border-t border-white/5 mt-3">
              <div class="grid grid-cols-1 sm:grid-cols-2 gap-3">
                <div>
                  <label class="block text-[11px] font-medium text-slate-400 mb-1">GitHub Owner / Org</label>
                  <input id="owner" type="text" value="89Aman"
                    class="w-full bg-dark-950 border border-white/10 rounded-lg px-3 py-2 text-xs font-mono text-white focus:outline-none focus:border-brand-500" />
                </div>
                <div>
                  <label class="block text-[11px] font-medium text-slate-400 mb-1">Admin API Token</label>
                  <div class="relative flex items-center">
                    <input id="adminToken" type="password" placeholder="ADMIN_API_TOKEN"
                      class="w-full bg-dark-950 border border-white/10 rounded-lg pl-3 pr-8 py-2 text-xs font-mono text-white focus:outline-none focus:border-brand-500" />
                    <button type="button" onclick="toggleVisibility('adminToken', 'tokenEyeIcon')" class="absolute right-2 text-slate-500 hover:text-slate-300">
                      <span id="tokenEyeIcon" class="material-symbols-outlined text-xs">visibility</span>
                    </button>
                  </div>
                </div>
              </div>

              <div>
                <label class="block text-[11px] font-medium text-slate-400 mb-1">Base URL Override (Optional)</label>
                <input id="baseUrl" type="text" placeholder="https://ollama.my-tunnel.com/v1"
                  class="w-full bg-dark-950 border border-white/10 rounded-lg px-3 py-2 text-xs font-mono text-white focus:outline-none focus:border-brand-500" />
              </div>

              <div>
                <div class="flex items-center justify-between mb-1">
                  <label class="text-[11px] font-medium text-slate-400">Custom Review Policy (Markdown)</label>
                  <div class="flex items-center gap-1.5 text-[10px]">
                    <button type="button" onclick="applyPolicyTemplate('security')" class="text-brand-400 hover:underline">Security</button>
                    <span class="text-slate-600">•</span>
                    <button type="button" onclick="applyPolicyTemplate('perf')" class="text-brand-400 hover:underline">Perf</button>
                    <span class="text-slate-600">•</span>
                    <button type="button" onclick="applyPolicyTemplate('strict')" class="text-brand-400 hover:underline">Strict</button>
                  </div>
                </div>
                <textarea id="reviewPolicy" rows="4"
                  class="w-full bg-dark-950 border border-white/10 rounded-lg p-3 text-xs font-mono text-slate-300 focus:outline-none focus:border-brand-500 leading-relaxed resize-none"
                  placeholder="Optional custom guidelines (e.g. enforce typing, check timeouts)..."></textarea>
              </div>
            </div>
          </details>

          <!-- Hidden legacy alertBox for backwards compatibility -->
          <div id="alertBox" class="hidden"></div>

          <!-- Action Controls -->
          <div class="flex items-center justify-between gap-3 pt-2">
            <button type="button" onclick="loadVault()"
              class="text-xs text-slate-500 hover:text-slate-300 transition flex items-center gap-1">
              <span class="material-symbols-outlined text-sm">sync</span>
              <span>Reload Config</span>
            </button>
            
            <div class="flex items-center gap-2">
              <button type="button" onclick="testConnection()"
                class="px-4 py-2 rounded-xl bg-dark-850 hover:bg-dark-800 text-slate-300 border border-white/10 text-xs font-medium transition flex items-center gap-1.5 active:scale-95">
                <span class="material-symbols-outlined text-amber-400 text-sm">bolt</span>
                <span>Test Connection</span>
              </button>
              <button type="submit"
                class="px-5 py-2 rounded-xl bg-brand-500 hover:bg-brand-600 text-white font-display text-xs font-semibold shadow-md shadow-brand-500/20 transition flex items-center gap-1.5 active:scale-95">
                <span>Save Credentials</span>
              </button>
            </div>
          </div>

        </form>

      </div>

      <!-- Right Column: Quick Commands & Live Probe Terminal (lg:col-span-5) -->
      <div class="lg:col-span-5 space-y-6">
        
        <!-- Live Probe Terminal Box -->
        <div class="rounded-2xl bg-dark-900 border border-white/10 p-5 space-y-3">
          <div class="flex items-center justify-between border-b border-white/5 pb-2.5">
            <div class="flex items-center gap-2">
              <span class="material-symbols-outlined text-brand-400">terminal</span>
              <h2 class="font-display font-bold text-white text-xs sm:text-sm">Connection Telemetry</h2>
            </div>
            <span class="text-[10px] font-mono px-2 py-0.5 rounded bg-white/5 text-slate-400 border border-white/5">TLS 1.3</span>
          </div>

          <div id="testConsole" class="rounded-xl bg-dark-950 border border-white/5 p-3.5 font-mono text-xs space-y-1.5 min-h-[110px] text-slate-400 overflow-y-auto">
            <p class="text-slate-600">// Click "Test Connection" to probe your configured provider.</p>
            <p class="text-slate-600">// Validates latency and key authorization in real time.</p>
          </div>
        </div>

        <!-- Copilot Commands Quick Card with Copy Buttons -->
        <div class="rounded-2xl bg-dark-900 border border-white/10 p-5 space-y-3">
          <div class="flex items-center justify-between border-b border-white/5 pb-2.5">
            <div class="flex items-center gap-2">
              <span class="material-symbols-outlined text-brand-400">chat</span>
              <h3 class="font-display font-bold text-white text-xs sm:text-sm">Interactive Commands</h3>
            </div>
            <a href="/docs" class="text-xs text-brand-400 hover:underline">Full Docs →</a>
          </div>

          <div class="space-y-2 text-xs">
            <div class="p-2.5 rounded-xl bg-dark-950 border border-white/5 flex items-center justify-between gap-2">
              <div>
                <code class="text-brand-400 font-mono font-semibold">@PatchPilot /review</code>
                <p class="text-[11px] text-slate-400">Triggers complete line-by-line audit</p>
              </div>
              <button onclick="copyCommand('@PatchPilot /review')" class="px-2 py-1 rounded bg-white/5 hover:bg-white/10 text-slate-400 hover:text-white text-[10px] font-mono transition">
                Copy
              </button>
            </div>

            <div class="p-2.5 rounded-xl bg-dark-950 border border-white/5 flex items-center justify-between gap-2">
              <div>
                <code class="text-brand-400 font-mono font-semibold">@PatchPilot /explain</code>
                <p class="text-[11px] text-slate-400">Plain-English architectural breakdown</p>
              </div>
              <button onclick="copyCommand('@PatchPilot /explain')" class="px-2 py-1 rounded bg-white/5 hover:bg-white/10 text-slate-400 hover:text-white text-[10px] font-mono transition">
                Copy
              </button>
            </div>

            <div class="p-2.5 rounded-xl bg-dark-950 border border-white/5 flex items-center justify-between gap-2">
              <div>
                <code class="text-brand-400 font-mono font-semibold">@PatchPilot /fix</code>
                <p class="text-[11px] text-slate-400">Generates 1-click commit suggestion</p>
              </div>
              <button onclick="copyCommand('@PatchPilot /fix')" class="px-2 py-1 rounded bg-white/5 hover:bg-white/10 text-slate-400 hover:text-white text-[10px] font-mono transition">
                Copy
              </button>
            </div>

            <div class="p-2.5 rounded-xl bg-dark-950 border border-white/5 flex items-center justify-between gap-2">
              <div>
                <code class="text-brand-400 font-mono font-semibold">@PatchPilot /test</code>
                <p class="text-[11px] text-slate-400">Synthesizes edge-case unit tests</p>
              </div>
              <button onclick="copyCommand('@PatchPilot /test')" class="px-2 py-1 rounded bg-white/5 hover:bg-white/10 text-slate-400 hover:text-white text-[10px] font-mono transition">
                Copy
              </button>
            </div>
          </div>
        </div>

      </div>

    </div>

  </main>

  <!-- Footer -->
  <footer class="border-t border-white/5 py-6 text-center text-xs text-slate-500 font-mono mt-auto">
    <div class="max-w-6xl mx-auto px-6 flex flex-col sm:flex-row items-center justify-between gap-4">
      <div>
        <span class="font-display font-bold text-slate-300">PatchPilot</span> &bull; 
        <span>Sovereign BYOK Code Review Vault</span>
      </div>
      <div class="flex items-center gap-4 text-slate-400">
        <a href="/privacy" class="hover:text-white transition">Privacy Policy</a>
        <a href="/terms" class="hover:text-white transition">Terms of Service</a>
        <a href="/docs" class="hover:text-white transition">Docs</a>
        <a href="https://github.com/89Aman/PatchPilot" target="_blank" rel="noreferrer" class="hover:text-white transition">GitHub</a>
      </div>
    </div>
  </footer>
  <script>
    const PRESET_MODELS = {
      gemini: ["gemini-2.5-pro", "gemini-2.5-flash"],
      openrouter: ["anthropic/claude-3.7-sonnet", "deepseek/deepseek-chat-v3-0324", "openai/gpt-4o", "x-ai/grok-3"],
      openai: ["gpt-4o", "gpt-4.1", "o3-mini"],
      groq: ["llama-3.3-70b-versatile", "llama-3.1-8b-instant", "openai/gpt-oss-120b"],
      grok: ["grok-3", "grok-3-mini", "grok-2-latest"],
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
      
      requestAnimationFrame(() => {
        toast.classList.remove("translate-y-2", "opacity-0");
      });

      setTimeout(() => {
        toast.classList.add("opacity-0", "translate-y-2");
        setTimeout(() => toast.remove(), 300);
      }, 3500);
    }

    function copyCommand(cmd) {
      navigator.clipboard.writeText(cmd).then(() => {
        showToast(`Copied to clipboard: ${cmd}`, "info");
      }).catch(() => {
        showToast(`Copied: ${cmd}`, "info");
      });
    }

    function applyPolicyTemplate(type) {
      if (POLICY_TEMPLATES[type]) {
        document.getElementById("reviewPolicy").value = POLICY_TEMPLATES[type];
        showToast(`Applied ${type.toUpperCase()} template`, "info");
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
      const select = document.getElementById("provider");
      select.value = key;
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
      if (PRESET_MODELS[provider]?.length) {
        document.getElementById("model").value = PRESET_MODELS[provider][0];
      }
    }

    let clerkInstance = null;

    async function initClerk() {
      try {
        const res = await fetch("/auth/clerk/config");
        const cfg = await res.json();
        if (!cfg.publishable_key) return;

        const domain = cfg.frontend_api || "clerk.noventra.org.in";

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
              s2.setAttribute("data-clerk-publishable-key", cfg.publishable_key);
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
            const loadOptions = { publishableKey: cfg.publishable_key };
            if (cfg.frontend_api) {
              loadOptions.frontendApi = cfg.frontend_api;
            }
            await clerkInstance.load(loadOptions);
          }
          if (clerkInstance.user) {
            const token = await clerkInstance.session.getToken();
            await fetch("/auth/clerk/session", {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({
                session_token: token,
                user: {
                  login: clerkInstance.user.username || clerkInstance.user.primaryEmailAddress?.emailAddress?.split("@")[0] || clerkInstance.user.id,
                  name: clerkInstance.user.fullName || clerkInstance.user.username,
                  avatar_url: clerkInstance.user.imageUrl,
                }
              })
            });
            await checkAuth();
            return;
          }
        }
      } catch (err) {
        console.debug("Clerk init:", err);
      }
    }

    async function handleClerkAuth() {
      const target = encodeURIComponent(`${window.location.origin}/dashboard`);
      window.location.href = `https://accounts.noventra.org.in/sign-in?sign_in_force_redirect_url=${target}&sign_up_force_redirect_url=${target}&redirect_url=${target}`;
    }

    async function signOutUser() {
      if (clerkInstance) {
        try { await clerkInstance.signOut(); } catch (e) {}
      }
      window.location.href = "/auth/logout";
    }

    async function checkAuth() {
      try {
        const res = await fetch("/auth/me");
        const data = await res.json();
        const authBlock = document.getElementById("authHeaderBlock");
        if (data.logged_in && data.user) {
          document.getElementById("identityCard").classList.remove("hidden");
          document.getElementById("loginCallout").classList.add("hidden");
          document.getElementById("userAvatar").src = data.user.avatar_url;
          document.getElementById("userName").textContent = data.user.name || data.user.login;
          document.getElementById("userLogin").textContent = `@${data.user.login}`;
          document.getElementById("owner").value = data.user.login;
          const authBadge = document.getElementById("authBadge");
          if (authBadge) {
            const isClerk = data.provider === "clerk";
            authBadge.textContent = isClerk ? "SSO Verified" : "OAuth Verified";
            authBadge.className = isClerk
              ? "text-[10px] font-mono px-2 py-0.5 rounded bg-brand-500/10 text-brand-400 border border-brand-500/20"
              : "text-[10px] font-mono px-2 py-0.5 rounded bg-emerald-500/10 text-emerald-400 border border-emerald-500/20";
          }
          if (authBlock) {
            authBlock.innerHTML = `
              <div class="flex items-center gap-2 px-2.5 py-1 rounded-lg bg-white/5 border border-white/10 text-xs">
                <img src="${escapeHtml(data.user.avatar_url)}" class="w-5 h-5 rounded-full object-cover border border-white/10" alt="Avatar" />
                <span class="text-white font-mono text-[11px]">@${escapeHtml(data.user.login)}</span>
                <button onclick="signOutUser()" class="text-slate-400 hover:text-red-400 text-[11px] ml-1 flex items-center" title="Sign Out">
                  <span class="material-symbols-outlined text-xs">logout</span>
                </button>
              </div>
            `;
          }
          loadUserRepos();
        } else {
          document.getElementById("loginCallout").classList.remove("hidden");
          if (authBlock) {
            authBlock.innerHTML = `
              <button onclick="handleClerkAuth()" class="px-3 py-1.5 rounded-lg bg-brand-500 hover:bg-brand-600 text-white font-semibold text-xs transition flex items-center gap-1.5 shadow-md shadow-brand-500/20 active:scale-95">
                <span class="material-symbols-outlined text-xs">lock</span>
                <span>Sign In</span>
              </button>
            `;
          }
        }
      } catch (err) {
        console.error("Auth check failed:", err);
      }
    }

    function escapeHtml(str) {
      if (!str) return "";
      const div = document.createElement("div");
      div.textContent = str;
      return div.innerHTML;
    }

    async function connectGitHubAccount() {
      if (clerkInstance && clerkInstance.user) {
        try {
          showToast("Opening GitHub authorization...", "info");
          const res = await clerkInstance.user.createExternalAccount({
            strategy: "oauth_github",
            redirect_url: window.location.href,
          });
          if (res.verification?.externalVerificationRedirectURL) {
            window.location.href = res.verification.externalVerificationRedirectURL.href;
            return;
          }
        } catch (err) {
          console.warn("Clerk external account connection prompt:", err);
          openTokenModal();
        }
      } else {
        openTokenModal();
      }
    }

    function openTokenModal() {
      document.getElementById("githubTokenModal").classList.remove("hidden");
    }

    function closeTokenModal() {
      document.getElementById("githubTokenModal").classList.add("hidden");
    }

    async function submitGitHubToken() {
      const tokenInput = document.getElementById("githubTokenInput");
      const token = tokenInput.value.trim();
      if (!token) {
        showToast("Please enter a GitHub Personal Access Token", "error");
        return;
      }
      showToast("Verifying token and checking repository scopes...", "info");
      try {
        const res = await fetch("/api/user/github-token", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ token }),
        });
        if (!res.ok) {
          const err = await res.json();
          showToast(`GitHub connection failed: ${err.detail || "Invalid token"}`, "error");
          return;
        }
        const data = await res.json();
        closeTokenModal();
        tokenInput.value = "";
        showToast(`✓ Connected as @${data.github_login}! Repositories scoped.`, "success");
        document.getElementById("owner").value = data.github_login;
        await checkAuth();
        await loadUserRepos();
      } catch (e) {
        showToast(`Error: ${e.message}`, "error");
      }
    }

    async function loadUserRepos() {
      const pillContainer = document.getElementById("repoPills");
      try {
        const res = await fetch("/api/user/repos");
        if (!res.ok) {
          pillContainer.innerHTML = '<span class="text-slate-500 text-[11px] font-mono">No repositories detected.</span>';
          return;
        }
        const data = await res.json();
        const repos = Array.isArray(data) ? data : (data.repos || []);
        const ghConnected = Array.isArray(data) ? true : (data.github_connected !== false);

        if (!ghConnected || (!repos.length && data.github_connected === false)) {
          pillContainer.innerHTML = `
            <div class="flex items-center gap-1.5">
              <span class="text-amber-400 text-[10px] font-mono">GitHub not connected</span>
              <button type="button" onclick="connectGitHubAccount()" class="px-2 py-0.5 rounded bg-brand-500/20 hover:bg-brand-500/30 text-brand-300 border border-brand-500/30 text-[10px] font-mono transition flex items-center gap-1">
                <span class="material-symbols-outlined text-xs">link</span>
                <span>Connect GitHub</span>
              </button>
              <button type="button" onclick="openTokenModal()" class="px-2 py-0.5 rounded bg-white/5 hover:bg-white/10 text-slate-400 text-[10px] font-mono transition">
                PAT
              </button>
            </div>
          `;
          return;
        }

        if (!repos.length) {
          pillContainer.innerHTML = `
            <span class="text-slate-400 text-[11px] font-mono">0 repositories scoped</span>
            <a href="https://github.com/apps/patchpilot-app/installations/new" target="_blank" rel="noopener noreferrer" class="px-2.5 py-1 rounded-lg bg-brand-500 hover:bg-brand-600 text-white text-[11px] font-semibold transition shadow-md shadow-brand-500/20 flex items-center gap-1">
              <span class="material-symbols-outlined text-xs">add_moderator</span>
              <span>Install &amp; Scope Repos</span>
            </a>
          `;
          return;
        }

        pillContainer.innerHTML = repos.slice(0, 3).map(r => {
          const safeName = escapeHtml(r.full_name || r.name);
          const safeUrl = (r.html_url && r.html_url.startsWith("https://github.com/")) ? r.html_url : `https://github.com/${safeName}`;
          return `<a href="${safeUrl}" target="_blank" rel="noopener noreferrer" class="px-2 py-0.5 rounded bg-white/5 border border-white/5 hover:border-brand-500 text-slate-300 text-[10px] font-mono transition">${safeName}</a>`;
        }).join("") + (repos.length > 3 ? `<span class="text-slate-500 text-[10px] font-mono">+${repos.length - 3} more</span>` : '') + `
          <a href="https://github.com/settings/installations" target="_blank" rel="noopener noreferrer" class="ml-1 text-[10px] text-brand-400 hover:text-brand-300 font-mono transition flex items-center gap-0.5" title="Manage repository scope">
            <span class="material-symbols-outlined text-xs">tune</span>
            <span>Configure Scope</span>
          </a>
          <a href="https://github.com/apps/patchpilot-app/installations/new" target="_blank" rel="noopener noreferrer" class="ml-1 text-[10px] text-brand-400 hover:text-brand-300 font-mono transition flex items-center gap-0.5" title="Install on more repositories">
            <span class="material-symbols-outlined text-xs">add</span>
            <span>Add Repos</span>
          </a>
        `;
      } catch (err) {
        pillContainer.innerHTML = `<span class="text-red-400 text-xs font-mono">Error: ${escapeHtml(String(err))}</span>`;
      }
    }

    async function loadVault() {
      const owner = document.getElementById("owner").value.trim();
      const token = document.getElementById("adminToken").value.trim();
      localStorage.setItem("cl_admin_token", token);
      localStorage.setItem("cl_owner", owner);

      const headers = {};
      if (token) headers["X-Admin-Token"] = token;

      const res = await fetch(`/vault/${owner}`, { headers });
      if (!res.ok) {
        showToast(`Failed to load config (${res.status})`, "error");
        return;
      }
      const data = await res.json();
      document.getElementById("provider").value = data.provider;
      onProviderChange();
      document.getElementById("model").value = data.model;
      document.getElementById("baseUrl").value = data.base_url || "";
      document.getElementById("reviewPolicy").value = data.review_policy || "";
      const statusBadge = document.getElementById("keyStatusBadge");
      const keyInput = document.getElementById("apiKey");
      if (data.has_api_key) {
        statusBadge.innerHTML = '<span class="w-1.5 h-1.5 rounded-full bg-emerald-400"></span><span>Key Active (Encrypted)</span>';
        statusBadge.className = "text-xs font-mono px-2.5 py-1 rounded-full bg-emerald-500/10 text-emerald-400 border border-emerald-500/20 flex items-center gap-1.5";
        document.getElementById("keyHint").textContent = "(Saved in Vault)";
        if (data.masked_api_key) {
          keyInput.value = data.masked_api_key;
          keyInput.dataset.savedMasked = data.masked_api_key;
        }
      } else {
        statusBadge.innerHTML = '<span class="w-1.5 h-1.5 rounded-full bg-amber-400"></span><span>No Key Stored</span>';
        statusBadge.className = "text-xs font-mono px-2.5 py-1 rounded-full bg-amber-500/10 text-amber-400 border border-amber-500/20 flex items-center gap-1.5";
        document.getElementById("keyHint").textContent = "(No Key Set)";
        keyInput.value = "";
        delete keyInput.dataset.savedMasked;
      }
      showToast(`Loaded configuration for '${data.owner}'`, "info");
    }

    async function saveVault() {
      const owner = document.getElementById("owner").value.trim();
      const token = document.getElementById("adminToken").value.trim();
      localStorage.setItem("cl_admin_token", token);
      localStorage.setItem("cl_owner", owner);

      const headers = { "Content-Type": "application/json" };
      if (token) headers["X-Admin-Token"] = token;

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

      const res = await fetch(`/vault/${owner}`, {
        method: "PUT",
        headers,
        body: JSON.stringify(payload)
      });

      if (!res.ok) {
        showToast(`Save failed (${res.status}): ${await res.text()}`, "error");
        return;
      }
      const data = await res.json();
      if (data.masked_api_key) {
        keyInput.value = data.masked_api_key;
        keyInput.dataset.savedMasked = data.masked_api_key;
      }
      const statusBadge = document.getElementById("keyStatusBadge");
      statusBadge.innerHTML = '<span class="w-1.5 h-1.5 rounded-full bg-emerald-400"></span><span>Key Active (Encrypted)</span>';
      statusBadge.className = "text-xs font-mono px-2.5 py-1 rounded-full bg-emerald-500/10 text-emerald-400 border border-emerald-500/20 flex items-center gap-1.5";
      document.getElementById("keyHint").textContent = "(Saved in Vault)";
      showToast("Credentials encrypted with AES-256-GCM and saved.", "success");
    }

    async function testConnection() {
      await saveVault();
      const owner = document.getElementById("owner").value.trim();
      const token = document.getElementById("adminToken").value.trim();
      const headers = {};
      if (token) headers["X-Admin-Token"] = token;

      const consoleBox = document.getElementById("testConsole");
      const startTime = performance.now();
      consoleBox.innerHTML = `
        <div class="flex items-center gap-2 text-brand-400">
          <span class="w-2 h-2 rounded-full bg-brand-400 animate-ping"></span>
          <span>Probing ${document.getElementById("provider").value} [${document.getElementById("model").value}]...</span>
        </div>
      `;

      showToast("Testing model connection probe...", "info");
      const res = await fetch(`/vault/${owner}/test`, {
        method: "POST",
        headers
      });
      const latency = Math.round(performance.now() - startTime);
      const data = await res.json();
      
      if (!res.ok) {
        showToast(`Connection probe failed (${latency}ms)`, "error");
        consoleBox.innerHTML = `
          <div class="text-red-400 font-semibold">✗ Probe Failed (${latency}ms)</div>
          <div class="text-red-300 text-[11px] bg-red-950/40 p-2 rounded border border-red-900/60 mt-1">${escapeHtml(data.detail || JSON.stringify(data))}</div>
        `;
        return;
      }

      showToast(`✓ Probe verified in ${latency}ms!`, "success");
      consoleBox.innerHTML = `
        <div class="text-emerald-400 font-semibold flex items-center gap-1.5">
          <span>✓ Handshake Verified</span>
          <span class="text-slate-500 font-normal">(${latency}ms)</span>
        </div>
        <div class="text-slate-300 mt-1">Provider: <span class="text-white">${data.provider}</span></div>
        <div class="text-slate-300">Model: <span class="text-white">${data.model}</span></div>
        <div class="mt-2 text-[11px] bg-dark-950 border border-white/5 p-2 rounded text-emerald-300">
          "${escapeHtml(data.reply)}"
        </div>
      `;
    }

    window.addEventListener("DOMContentLoaded", () => {
      document.getElementById("adminToken").value = localStorage.getItem("cl_admin_token") || "";
      document.getElementById("owner").value = localStorage.getItem("cl_owner") || "89Aman";
      onProviderChange();
      initClerk();
      checkAuth();
      if (document.getElementById("adminToken").value) loadVault();
    });
  </script>
</body>
</html>
"""