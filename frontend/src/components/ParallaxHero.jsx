import React from 'react';
import '../styles/landing.css';

const ParallaxHero = ({ onLoginClick, chromeUrl, screenshotUrl }) => {
  return (
    <section className="relative min-h-[92vh] flex items-center overflow-hidden">
      <div className="parallax-bg" aria-hidden="true" />
      <div className="relative z-10 container mx-auto px-5 grid md:grid-cols-2 gap-10 items-center">
        <div>
          <h1 className="text-4xl sm:text-6xl font-black leading-tight">
            Trade smarter.<br /><span className="text-[#2bf57a]">Profit faster.</span>
          </h1>
          <p className="mt-3 text-lg text-slate-300 max-w-xl">
            Your all-in-one dashboard for EA FC trading — live prices, trend scans,
            portfolio profit, and automated alerts.
          </p>
          <div className="flex flex-wrap gap-3 mt-6">
            <a
              className="btn-solid-primary rounded-xl px-4 py-3 font-extrabold shadow"
              href="#login"
              onClick={(e)=>{e.preventDefault(); onLoginClick?.();}}
            >
              Login to Dashboard
            </a>
            <a
              className="btn-solid-secondary rounded-xl px-4 py-3 font-extrabold shadow"
              href={chromeUrl}
              target="_blank" rel="noreferrer"
            >
              Add Chrome Extension
            </a>
          </div>
          <ul className="flex flex-wrap gap-4 mt-4 text-slate-300">
            <li>⚡ Fast • <span className="text-slate-400">no fluff UI</span></li>
            <li>🔒 Secure • <span className="text-slate-400">Discord OAuth</span></li>
            <li>🟩 Console focused • <span className="text-slate-400">PS &amp; Xbox</span></li>
          </ul>
        </div>
        <figure className="card-surface rounded-2xl p-2">
          <img src={screenshotUrl} alt="FUT Trader dashboard preview" className="rounded-xl w-full h-auto" />
        </figure>
      </div>
      <a href="#features" className="absolute bottom-6 left-1/2 -translate-x-1/2 text-slate-400 font-bold">
        <div className="flex flex-col items-center">
          <span>Scroll</span>
          <svg width="22" height="22" viewBox="0 0 24 24" fill="currentColor"><path d="M12 16l-6-6h12l-6 6z"/></svg>
        </div>
      </a>
    </section>
  );
};

export default ParallaxHero;
