// src/v2/pages/DiscordFeed/DiscordFeed.jsx
//
// Web management page for the self-service Discord Server Feed, backing
// GET/PATCH/POST /api/v2/discord-feed/* (backend/app/routers/discord_feed_web.py).
// Everything here is also available via the /feed Discord command group -
// this page is a convenience, not a replacement, and both surfaces drive
// the exact same discord_feed_billing.py functions.
//
// Access is server-enforced: the backend checks, live against Discord on
// every request, that the signed-in user currently has Manage Server in
// the selected guild - this page never relies on a client-side guess.
import { useEffect, useState } from "react";
import { AlertTriangle, Check, Loader2, Pause, Play, RefreshCw, Send } from "lucide-react";
import api from "../../../axios";
import SectionCard from "../../components/SectionCard";
import { PageHead } from "../Players/Players";
import "./discord-feed.css";

const PLATFORM_OPTIONS = [
  { value: "console", label: "Console" },
  { value: "pc", label: "PC" },
  { value: "both", label: "Both" },
];

const BUDGET_MIX_OPTIONS = [
  { value: "balanced", label: "Balanced" },
  { value: "mostly_low", label: "Mostly low-budget" },
  { value: "mostly_medium", label: "Mostly medium-budget" },
  { value: "mostly_high", label: "Mostly high-budget" },
];

const MIN_CONFIDENCE_OPTIONS = [0.5, 0.6, 0.7, 0.8];
const MIN_PROFIT_OPTIONS = [1000, 2500, 5000, 10000, 25000];

// Same billing states Billing.jsx's SERVER_STATUS_LABEL humanizes - kept
// in sync so a server's subscription reads the same plain-language way
// on both pages instead of leaking the raw Stripe status here.
const SUBSCRIPTION_STATUS_LABEL = {
  active: "Active",
  trialing: "Trial",
  past_due: "Payment failed",
  checkout_pending: "Checkout not finished",
  unpaid: "Not paid",
  canceled: "Cancelled",
};

const HOURS = Array.from({ length: 24 }, (_, h) => h);

function hourLabel(h) {
  return `${String(h).padStart(2, "0")}:00`;
}

export default function DiscordFeed() {
  const [guilds, setGuilds] = useState(null); // null = loading, [] = none
  const [selectedGuildId, setSelectedGuildId] = useState(null);
  const [feedState, setFeedState] = useState(null); // { feed, posts_used_today, channel_ok, channel_problem }
  const [channels, setChannels] = useState(null);
  const [roles, setRoles] = useState(null);
  const [loadError, setLoadError] = useState("");
  const [busy, setBusy] = useState(false);
  const [actionMsg, setActionMsg] = useState("");
  const [actionErr, setActionErr] = useState("");

  // Draft preference values, seeded from the loaded feed once it arrives.
  const [platformScope, setPlatformScope] = useState("");
  const [budgetMix, setBudgetMix] = useState("");
  const [minConfidence, setMinConfidence] = useState("");
  const [minProfit, setMinProfit] = useState("");
  const [windowEnabled, setWindowEnabled] = useState(false);
  const [startHour, setStartHour] = useState(8);
  const [endHour, setEndHour] = useState(23);
  const [tz, setTz] = useState("Europe/London");
  const [buyPingRole, setBuyPingRole] = useState("");
  const [sellPingRole, setSellPingRole] = useState("");

  useEffect(() => {
    loadGuilds();
  }, []);

  useEffect(() => {
    if (selectedGuildId) loadFeed(selectedGuildId);
  }, [selectedGuildId]);

  async function loadGuilds() {
    setLoadError("");
    try {
      const { data } = await api.get("/api/v2/discord-feed/guilds");
      const list = data?.guilds || [];
      setGuilds(list);
      if (list.length === 1) setSelectedGuildId(list[0].guild_id);
    } catch (e) {
      setGuilds([]);
      setLoadError(
        e?.response?.status === 401
          ? "Sign in to manage a Server Feed."
          : "Couldn't load your Discord servers. Try again shortly."
      );
    }
  }

  async function loadFeed(guildId) {
    setFeedState(null);
    setChannels(null);
    setRoles(null);
    setActionMsg("");
    setActionErr("");
    try {
      const { data } = await api.get(`/api/v2/discord-feed/${guildId}`);
      setFeedState(data);
      const f = data.feed;
      setPlatformScope(f.platform_scope || "");
      setBudgetMix(f.budget_mix || "");
      setMinConfidence(f.min_confidence != null ? String(f.min_confidence) : "");
      setMinProfit(f.min_expected_profit != null ? String(f.min_expected_profit) : "");
      setBuyPingRole(f.buy_ping_role_id || "");
      setSellPingRole(f.sell_ping_role_id || "");
      const hasWindow = f.posting_window_start_hour != null && f.posting_window_end_hour != null;
      setWindowEnabled(hasWindow);
      setStartHour(hasWindow ? f.posting_window_start_hour : 8);
      setEndHour(hasWindow ? f.posting_window_end_hour : 23);
      setTz(f.posting_timezone || "Europe/London");
      api.get(`/api/v2/discord-feed/${guildId}/channels`).then(
        (res) => setChannels(res.data?.channels || []),
        () => setChannels([])
      );
      api.get(`/api/v2/discord-feed/${guildId}/roles`).then(
        (res) => setRoles(res.data?.roles || []),
        () => setRoles([])
      );
    } catch (e) {
      setLoadError(e?.response?.data?.detail || "Couldn't load this server's feed.");
    }
  }

  async function runAction(fn, successMsg) {
    setBusy(true);
    setActionMsg("");
    setActionErr("");
    try {
      await fn();
      setActionMsg(successMsg);
      await loadFeed(selectedGuildId);
    } catch (e) {
      setActionErr(e?.response?.data?.detail || "That didn't work - please try again.");
    } finally {
      setBusy(false);
    }
  }

  function savePreferences() {
    runAction(
      () =>
        api.patch(`/api/v2/discord-feed/${selectedGuildId}/preferences`, {
          platform_scope: platformScope || null,
          budget_mix: budgetMix || null,
          min_confidence: minConfidence ? Number(minConfidence) : null,
          min_expected_profit: minProfit ? Number(minProfit) : null,
          posting_window_start_hour: windowEnabled ? Number(startHour) : null,
          posting_window_end_hour: windowEnabled ? Number(endHour) : null,
          posting_timezone: windowEnabled ? tz : null,
          clear_posting_window: !windowEnabled,
        }),
      "Preferences saved."
    );
  }

  function savePingRoles() {
    runAction(
      () =>
        api.patch(`/api/v2/discord-feed/${selectedGuildId}/ping-roles`, {
          buy_role_id: buyPingRole || null,
          sell_role_id: sellPingRole || null,
        }),
      "Role pings updated."
    );
  }

  function changeChannel(channelId) {
    runAction(
      () => api.patch(`/api/v2/discord-feed/${selectedGuildId}/channel`, { channel_id: channelId }),
      "Posting channel updated."
    );
  }

  function changeSellChannel(channelId) {
    runAction(
      () => api.patch(`/api/v2/discord-feed/${selectedGuildId}/sell-channel`, { channel_id: channelId || null }),
      channelId ? "Sell-time channel updated." : "Sell-time channel cleared - results will post in your main feed channel again."
    );
  }

  async function goToBilling() {
    try {
      const { data } = await api.get(`/api/v2/discord-feed/${selectedGuildId}/billing-url`);
      if (data?.url) window.location.href = data.url;
    } catch (e) {
      setActionErr(e?.response?.data?.detail || "Couldn't open billing.");
    }
  }

  async function goToCheckout() {
    try {
      const { data } = await api.get(`/api/v2/discord-feed/${selectedGuildId}/checkout-url`);
      if (data?.url) window.location.href = data.url;
    } catch (e) {
      setActionErr(e?.response?.data?.detail || "Couldn't start checkout.");
    }
  }

  if (guilds === null) {
    return (
      <main className="v2-destination discord-feed-v2">
        <div className="v2-page-state" role="status"><Loader2 className="spin" size={16} /> Loading…</div>
      </main>
    );
  }

  return (
    <main className="v2-destination discord-feed-v2">
      <PageHead title="Server Feed" />
      <header className="discord-feed-head">
        <h1>Discord Server Feed</h1>
        <p>Manage your server's paid trading feed. Every setting here also works via the <code>/feed</code> Discord command.</p>
      </header>

      {loadError ? <div className="discord-feed-msg err"><AlertTriangle size={15} /> {loadError}</div> : null}

      {guilds.length === 0 && !loadError ? (
        <SectionCard>
          <p>You don't currently have Manage Server in any Discord server with a Server Feed set up.</p>
          <p>Run <code>/feed setup</code> in your server (you'll need Manage Server there) to get started.</p>
          <a className="discord-feed-guide-link" href="https://eafc.live/#/guides/discord-server-feed" target="_blank" rel="noreferrer">
            Open the setup guide
          </a>
        </SectionCard>
      ) : null}

      {guilds.length > 1 ? (
        <SectionCard title="Choose a server">
          <div className="discord-feed-guild-picker">
            {guilds.map((g) => (
              <button
                key={g.guild_id}
                type="button"
                className={g.guild_id === selectedGuildId ? "active" : ""}
                onClick={() => setSelectedGuildId(g.guild_id)}
              >
                {g.name}
              </button>
            ))}
          </div>
        </SectionCard>
      ) : null}

      {selectedGuildId && !feedState ? (
        <div className="v2-page-state" role="status"><Loader2 className="spin" size={16} /> Loading feed…</div>
      ) : null}

      {feedState ? (
        <>
          {actionMsg ? <div className="discord-feed-msg ok"><Check size={15} /> {actionMsg}</div> : null}
          {actionErr ? <div className="discord-feed-msg err"><AlertTriangle size={15} /> {actionErr}</div> : null}

          <SectionCard title="Status">
            <dl className="discord-feed-status-grid">
              <div><dt>Plan</dt><dd>{feedState.feed.plan === "server_feed_plus" ? "Server Feed+" : "Server Feed"}</dd></div>
              <div><dt>Subscription</dt><dd>{SUBSCRIPTION_STATUS_LABEL[feedState.feed.subscription_status] || feedState.feed.subscription_status || "Unknown"}</dd></div>
              <div><dt>State</dt><dd>{feedState.feed.status === "active" ? "Active" : "Paused"}</dd></div>
              <div><dt>Posts today</dt><dd>{feedState.posts_used_today} of up to {feedState.feed.max_posts_per_day ?? "-"}</dd></div>
            </dl>
            {!feedState.channel_ok ? (
              <div className="discord-feed-msg err"><AlertTriangle size={15} /> {feedState.channel_problem || "Channel permissions couldn't be verified."}</div>
            ) : null}
            <div className="discord-feed-actions">
              {feedState.feed.status === "active" ? (
                <button type="button" disabled={busy} onClick={() => runAction(() => api.post(`/api/v2/discord-feed/${selectedGuildId}/pause`), "Paused - your subscription is unaffected.")}>
                  <Pause size={15} /> Pause
                </button>
              ) : (
                <button type="button" disabled={busy} onClick={() => runAction(() => api.post(`/api/v2/discord-feed/${selectedGuildId}/resume`), "Resumed.")}>
                  <Play size={15} /> {feedState.feed.status === "paused" ? "Resume" : "Activate"}
                </button>
              )}
              <button type="button" disabled={busy} onClick={() => runAction(() => api.post(`/api/v2/discord-feed/${selectedGuildId}/test`), "Sent a test post to your channel.")}>
                <Send size={15} /> Send test post
              </button>
              {feedState.feed.subscription_status === "active" || feedState.feed.subscription_status === "trialing" ? (
                <button type="button" onClick={goToBilling}>Manage billing</button>
              ) : (
                <button type="button" onClick={goToCheckout}>Complete payment</button>
              )}
            </div>
          </SectionCard>

          <SectionCard title="Posting channel">
            {channels === null ? (
              <p>Loading channels…</p>
            ) : (
              <select
                value={feedState.feed.feed_channel_id || ""}
                disabled={busy}
                onChange={(e) => changeChannel(e.target.value)}
              >
                <option value="" disabled>Choose a channel</option>
                {channels.map((c) => (
                  <option key={c.id} value={c.id}>#{c.name}</option>
                ))}
              </select>
            )}
          </SectionCard>

          <SectionCard
            title="Sell-time channel"
            subtitle="Where TARGET HIT / result posts go. Leave as “Same as posting channel” to keep results in your main feed."
          >
            {channels === null ? (
              <p>Loading channels…</p>
            ) : (
              <select
                value={feedState.feed.sell_channel_id || ""}
                disabled={busy}
                onChange={(e) => changeSellChannel(e.target.value)}
              >
                <option value="">Same as posting channel</option>
                {channels.map((c) => (
                  <option key={c.id} value={c.id}>#{c.name}</option>
                ))}
              </select>
            )}
          </SectionCard>

          <SectionCard
            title="Role pings"
            subtitle="Optional. Ping a Discord role when a new trade is posted and when the result is posted."
          >
            {roles === null ? (
              <p>Loading roles…</p>
            ) : (
              <>
                <div className="discord-feed-form">
                  <label>
                    New trade ping
                    <select value={buyPingRole} onChange={(e) => setBuyPingRole(e.target.value)}>
                      <option value="">No role ping</option>
                      {roles.map((role) => (
                        <option key={role.id} value={role.id}>
                          @{role.name}{role.mentionable ? "" : " (role may need Mentionable enabled)"}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label>
                    Sell / result ping
                    <select value={sellPingRole} onChange={(e) => setSellPingRole(e.target.value)}>
                      <option value="">Same as new trade ping</option>
                      {roles.map((role) => (
                        <option key={role.id} value={role.id}>
                          @{role.name}{role.mentionable ? "" : " (role may need Mentionable enabled)"}
                        </option>
                      ))}
                    </select>
                  </label>
                </div>
                <p className="mt-2 text-xs text-[var(--v2-muted)]">
                  EAFC.Live only allows the exact role you choose here to be mentioned. It never enables @everyone, @here or user pings.
                </p>
                <button type="button" className="discord-feed-save" disabled={busy} onClick={savePingRoles}>
                  {busy ? <><Loader2 className="spin" size={15} /> Saving…</> : <><RefreshCw size={15} /> Save role pings</>}
                </button>
              </>
            )}
          </SectionCard>

          <SectionCard title="Preferences" subtitle="Changes apply immediately.">
            <div className="discord-feed-form">
              <label>
                Platform
                <select value={platformScope} onChange={(e) => setPlatformScope(e.target.value)}>
                  {PLATFORM_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
                </select>
              </label>
              <label>
                Budget mix
                <select value={budgetMix} onChange={(e) => setBudgetMix(e.target.value)}>
                  {BUDGET_MIX_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
                </select>
              </label>
              <label>
                Minimum confidence
                <select value={minConfidence} onChange={(e) => setMinConfidence(e.target.value)}>
                  {MIN_CONFIDENCE_OPTIONS.map((v) => <option key={v} value={v}>{Math.round(v * 100)}%</option>)}
                </select>
              </label>
              <label>
                Minimum expected profit
                <select value={minProfit} onChange={(e) => setMinProfit(e.target.value)}>
                  {MIN_PROFIT_OPTIONS.map((v) => <option key={v} value={v}>{v.toLocaleString()} coins</option>)}
                </select>
              </label>
            </div>

            <div className="discord-feed-window">
              <label className="discord-feed-window-toggle">
                <input type="checkbox" checked={windowEnabled} onChange={(e) => setWindowEnabled(e.target.checked)} />
                Only post between certain hours (e.g. avoid posting at 3am for a UK-only server)
              </label>
              {windowEnabled ? (
                <div className="discord-feed-window-fields">
                  <label>
                    From
                    <select value={startHour} onChange={(e) => setStartHour(Number(e.target.value))}>
                      {HOURS.map((h) => <option key={h} value={h}>{hourLabel(h)}</option>)}
                    </select>
                  </label>
                  <label>
                    To
                    <select value={endHour} onChange={(e) => setEndHour(Number(e.target.value))}>
                      {HOURS.map((h) => <option key={h} value={h}>{hourLabel(h)}</option>)}
                    </select>
                  </label>
                  <label>
                    Timezone
                    <input
                      type="text"
                      value={tz}
                      onChange={(e) => setTz(e.target.value)}
                      placeholder="Europe/London"
                    />
                  </label>
                </div>
              ) : null}
            </div>

            <button type="button" className="discord-feed-save" disabled={busy} onClick={savePreferences}>
              {busy ? <><Loader2 className="spin" size={15} /> Saving…</> : <><RefreshCw size={15} /> Save preferences</>}
            </button>
          </SectionCard>
        </>
      ) : null}
    </main>
  );
}
