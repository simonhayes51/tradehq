import { Link } from "react-router-dom";
import {
  ArrowLeft,
  ArrowRight,
  Bot,
  CheckCircle,
  Clock,
  CreditCard,
  HelpCircle,
  KeyRound,
  MessageSquare,
  Settings2,
  ShieldCheck,
  Smartphone,
  Terminal,
} from "lucide-react";
import { APP_ORIGIN, DISCORD_BOT_INSTALL_URL } from "../lib/domains";
import "../styles/guides.css";

const APP_SIGNUP = `${APP_ORIGIN}/#/signup`;

function Breadcrumbs({ current }) {
  return (
    <nav className="guide-breadcrumbs" aria-label="Breadcrumb">
      <Link to="/">Home</Link><span>/</span><Link to="/guides">Guides</Link>
      {current ? <><span>/</span><span>{current}</span></> : null}
    </nav>
  );
}

function GuideIntro({ eyebrow, title, text, time, children }) {
  return (
    <header className="guide-hero">
      <div className="guide-glow" />
      <div className="pub-container guide-hero-inner">
        <Breadcrumbs current={eyebrow === "GUIDES" ? null : eyebrow} />
        <div className="guide-kicker">{eyebrow}</div>
        <h1>{title}</h1>
        <p>{text}</p>
        {time ? <div className="guide-time"><Clock size={16} /> {time}</div> : null}
        {children}
      </div>
    </header>
  );
}

function GuideCard({ icon: Icon, title, text, to, meta }) {
  return (
    <Link className="guide-card" to={to}>
      <div className="guide-card-icon"><Icon size={22} /></div>
      <div className="guide-card-meta">{meta}</div>
      <h2>{title}</h2>
      <p>{text}</p>
      <span>Open guide <ArrowRight size={16} /></span>
    </Link>
  );
}

function Step({ number, title, children, image, alt, note }) {
  return (
    <article className="guide-step">
      <div className="guide-step-number">{String(number).padStart(2, "0")}</div>
      <div className="guide-step-content">
        <h2>{title}</h2>
        <div className="guide-step-copy">{children}</div>
        {note ? <div className="guide-note"><CheckCircle size={18} /> <span>{note}</span></div> : null}
        {image ? <figure className="guide-figure"><img src={image} alt={alt} loading="lazy" /></figure> : null}
      </div>
    </article>
  );
}

function RelatedGuides({ current }) {
  const guides = [
    ["getting-started", "Getting started", "Create your account and find your first trade."],
    ["discord-server-feed", "Set up Server Feed", "Add the bot and switch on automatic posts."],
    ["discord-commands", "Server Feed commands", "A plain-English command reference."],
  ].filter(([slug]) => slug !== current);
  return (
    <section className="guide-related">
      <div className="pub-container">
        <div className="guide-section-label">KEEP GOING</div>
        <h2>Related guides</h2>
        <div className="guide-related-grid">
          {guides.map(([slug, title, text]) => (
            <Link key={slug} to={`/guides/${slug}`}><strong>{title}</strong><span>{text}</span><ArrowRight size={17} /></Link>
          ))}
        </div>
      </div>
    </section>
  );
}

export function GuidesHome() {
  return (
    <main className="guide-root">
      <GuideIntro
        eyebrow="GUIDES"
        title="Get set up without the guesswork."
        text="Short, practical guides for creating your EAFC.Live account and adding the trading feed to a Discord server."
      />
      <section className="guide-hub">
        <div className="pub-container guide-card-grid">
          <GuideCard icon={Smartphone} meta="ABOUT 1 MINUTE" title="Getting started with EAFC.Live" text="Create a free account, choose your preferences and open your first live opportunities." to="/guides/getting-started" />
          <GuideCard icon={Bot} meta="ABOUT 3 MINUTES" title="Set up the Discord Server Feed" text="Invite the bot, choose your feed settings, test the channel and activate automatic posts." to="/guides/discord-server-feed" />
          <GuideCard icon={Terminal} meta="COMMAND REFERENCE" title="Discord Server Feed commands" text="What every /feed command does, who can use it and when you will need it." to="/guides/discord-commands" />
        </div>
      </section>
    </main>
  );
}

export function GettingStartedGuide() {
  return (
    <main className="guide-root">
      <GuideIntro
        eyebrow="GETTING STARTED"
        title="Your first EAFC.Live trade, step by step."
        text="You can create a free account and see your first market opportunities in about a minute. No card details and no EA login needed."
        time="About 1 minute"
      >
        <a className="pub-button" href={APP_SIGNUP}>Create free account <ArrowRight size={17} /></a>
      </GuideIntro>

      <section className="guide-body">
        <div className="pub-container guide-content">
          <aside className="guide-aside">
            <strong>Before you start</strong>
            <p>You only need a Discord account. EAFC.Live never asks for your EA password or access to your Ultimate Team account.</p>
          </aside>
          <div className="guide-steps">
            <Step number={1} title="Create your free account">
              <p>Choose <strong>Create free account</strong> on EAFC.Live. Free is permanent, not a trial, and you do not need to enter payment details.</p>
            </Step>
            <Step number={2} title="Sign in with Discord" note="Discord is used to identify your EAFC.Live account. It does not give us access to your EA account.">
              <p>Discord opens so you can approve the sign-in. Check that the application is <strong>EAFC.Live</strong>, then continue.</p>
            </Step>
            <Step number={3} title="Set your trading budget">
              <p>Choose the coin range you normally trade with. We use it to make the Trade Finder and recommendations more relevant. You can change it later.</p>
            </Step>
            <Step number={4} title="Choose whether you want price alerts">
              <p>Price alerts are optional. If you switch them on, you can follow cards and get notified when they reach the price you are waiting for.</p>
            </Step>
            <Step number={5} title="Open your first opportunities" image="/marketing/eafc-mobile-home.svg" alt="EAFC.Live mobile home screen showing a live Harry Kane opportunity">
              <p>Your Home screen shows the important numbers together: the maximum buy price, expected profit after EA tax, risk, confidence and how fresh the market data is.</p>
              <p>Free accounts receive two complete opportunities each day, plus player search, market prices, a watchlist and basic trade tracking.</p>
            </Step>
          </div>
        </div>
      </section>
      <section className="guide-cta"><div className="pub-container"><div><span>READY TO TRY IT?</span><h2>Start free and see the market for yourself.</h2></div><a className="pub-button pub-button-light" href={APP_SIGNUP}>Create free account <ArrowRight size={17} /></a></div></section>
      <RelatedGuides current="getting-started" />
    </main>
  );
}

export function DiscordServerFeedGuide() {
  return (
    <main className="guide-root">
      <GuideIntro
        eyebrow="DISCORD SERVER FEED"
        title="Set up automatic trading posts in your server."
        text="Add EAFC.Live, choose where trades should appear, send a test post and then switch the live feed on."
        time="About 3 minutes"
      >
        <a className="pub-button" href={DISCORD_BOT_INSTALL_URL} target="_blank" rel="noreferrer">Add to Discord <ArrowRight size={17} /></a>
      </GuideIntro>

      <section className="guide-body">
        <div className="pub-container guide-content">
          <aside className="guide-aside">
            <strong>What you need</strong>
            <ul>
              <li><ShieldCheck size={16} /> Manage Server permission</li>
              <li><MessageSquare size={16} /> A text or announcement channel</li>
              <li><CreditCard size={16} /> A payment method for the server plan</li>
            </ul>
            <p className="guide-aside-small">Personal Elite and Server Feed are separate subscriptions. Server Feed posts into the whole community.</p>
          </aside>
          <div className="guide-steps">
            <Step number={1} title="Add EAFC.Live to your server" image="/guides/discord-authorise.png" alt="Discord authorisation window for adding EAFC.Live to a server">
              <p>Choose <strong>Add to Discord</strong>, select your server from the list and press <strong>Authorize</strong>. Discord only shows servers where you have permission to add applications.</p>
            </Step>
            <Step number={2} title="Choose the posting channel">
              <p>Create a channel for the feed or use an existing one. EAFC.Live needs <strong>View Channel</strong>, <strong>Send Messages</strong> and <strong>Embed Links</strong> in that channel.</p>
            </Step>
            <Step number={3} title="Open the setup command" image="/guides/discord-command-menu.png" alt="Discord command menu showing the EAFC.Live feed commands">
              <p>Type <code>/feed</code> and select <code>/feed setup</code>. Only members with Manage Server permission can see and use the Server Feed management commands.</p>
            </Step>
            <Step number={4} title="Choose the plan and preferences" image="/guides/discord-feed-setup.png" alt="Completed EAFC.Live feed setup command with plan, channel, platform and budget mix selected">
              <p>Complete the four options shown by Discord:</p>
              <ul>
                <li><strong>Plan:</strong> Server Feed is up to 6 posts per day. Server Feed+ is up to 15.</li>
                <li><strong>Channel:</strong> where the opportunities should be posted.</li>
                <li><strong>Platform:</strong> Console, PC or both.</li>
                <li><strong>Budget mix:</strong> balanced, mostly low, medium or high-budget trades.</li>
              </ul>
              <figure className="guide-figure guide-figure-compact"><img src="/guides/discord-plan-choice.png" alt="Discord plan selector showing the Server Feed and Server Feed+ options" loading="lazy" /></figure>
            </Step>
            <Step number={5} title="Complete checkout" note="Billing belongs to the Discord server. It does not upgrade your personal EAFC.Live account.">
              <p>EAFC.Live replies with a secure checkout link for the plan you selected. Complete payment in Stripe, then return to Discord.</p>
            </Step>
            <Step number={6} title="Send a test post" image="/guides/discord-test-post.png" alt="EAFC.Live sample test trade posted successfully in Discord">
              <p>Run <code>/feed test</code>. A sample card should appear in your chosen channel. It is clearly marked as a test and is not a real trade.</p>
            </Step>
            <Step number={7} title="Activate the live feed" note="That is it. EAFC.Live will now post matching opportunities automatically.">
              <p>Once the test looks right, run <code>/feed activate</code>. Use <code>/feed status</code> at any time to check the plan, channel, daily allowance and whether the feed is active.</p>
            </Step>
          </div>
        </div>
      </section>

      <section className="guide-help-strip">
        <div className="pub-container">
          <HelpCircle size={26} />
          <div><h2>Nothing appeared after the test?</h2><p>Check the bot can view the channel, send messages and embed links. Then run <code>/feed test</code> again.</p></div>
          <a href="mailto:support@eafc.live">Contact support</a>
        </div>
      </section>
      <RelatedGuides current="discord-server-feed" />
    </main>
  );
}

const feedCommands = [
  ["/feed setup", "Choose a plan, posting channel, platform and budget mix."],
  ["/feed test", "Send a clearly labelled sample post to check the channel and permissions."],
  ["/feed activate", "Start live opportunity posts after checkout and testing."],
  ["/feed status", "See the current plan, feed state, channel, allowance and posts used today."],
  ["/feed channel", "Move future opportunity posts to a different channel."],
  ["/feed sell_channel", "Send 72-hour target-hit / target-not-reached results to a separate channel, or clear the setting."],
  ["/feed preferences", "Change platform, budget mix, minimum confidence, minimum profit or posting hours."],
  ["/feed pause", "Stop new posts without cancelling or changing billing."],
  ["/feed resume", "Restart a paused feed."],
  ["/feed billing", "Open a secure Stripe link to manage the server subscription."],
  ["/feed help", "Show a quick summary of the plans and available commands inside Discord."],
];

export function DiscordCommandsGuide() {
  return (
    <main className="guide-root">
      <GuideIntro
        eyebrow="COMMAND REFERENCE"
        title="Every Server Feed command, explained."
        text="The commands are grouped under /feed and are only available to people with Manage Server permission."
      />
      <section className="guide-body">
        <div className="pub-container guide-command-wrap">
          <div className="guide-command-intro">
            <Terminal size={25} />
            <div><h2>Server management commands</h2><p>Type <code>/feed</code> in Discord to see the available options.</p></div>
          </div>
          <div className="guide-command-list">
            {feedCommands.map(([command, description]) => (
              <div key={command}><code>{command}</code><p>{description}</p></div>
            ))}
          </div>

          <div className="guide-command-intro guide-command-intro-second">
            <Settings2 size={25} />
            <div><h2>Useful preference options</h2><p>You can change one setting without rebuilding the whole feed.</p></div>
          </div>
          <div className="guide-option-grid">
            <article><strong>Platform</strong><span>Console, PC or both.</span></article>
            <article><strong>Budget mix</strong><span>Balanced or weighted towards low, medium or high budgets.</span></article>
            <article><strong>Minimum confidence</strong><span>Only post opportunities that meet your chosen confidence floor.</span></article>
            <article><strong>Minimum profit</strong><span>Set the smallest expected profit worth posting.</span></article>
            <article><strong>Posting window</strong><span>Limit posts to chosen local hours and timezone.</span></article>
            <article><strong>Results channel</strong><span>Keep result updates separate from new opportunities.</span></article>
          </div>

          <div className="guide-permission-callout">
            <KeyRound size={22} />
            <div><strong>Cannot see the commands?</strong><p>Make sure you have Manage Server permission and that EAFC.Live is still installed in the server.</p></div>
          </div>
        </div>
      </section>
      <RelatedGuides current="discord-commands" />
    </main>
  );
}

export function GuideBackLink() {
  return <Link className="guide-back" to="/guides"><ArrowLeft size={16} /> All guides</Link>;
}
