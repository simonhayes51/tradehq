-- FC27 leak monitoring pipeline
-- One X API read feeds both Discord reposting and structured EAFC.Live signals.

CREATE TABLE IF NOT EXISTS leak_sources (
    id BIGSERIAL PRIMARY KEY,
    x_user_id TEXT UNIQUE NOT NULL,
    username TEXT NOT NULL,
    display_name TEXT,
    active BOOLEAN NOT NULL DEFAULT TRUE,
    last_tweet_id TEXT,
    followers_count BIGINT,
    historical_accuracy NUMERIC(5,4),
    verified_leaks INTEGER NOT NULL DEFAULT 0,
    correct_leaks INTEGER NOT NULL DEFAULT 0,
    last_polled_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_leak_sources_active
    ON leak_sources(active) WHERE active = TRUE;

CREATE TABLE IF NOT EXISTS leak_tweets (
    id BIGSERIAL PRIMARY KEY,
    tweet_id TEXT UNIQUE NOT NULL,
    source_id BIGINT NOT NULL REFERENCES leak_sources(id) ON DELETE CASCADE,
    tweeted_at TIMESTAMPTZ,
    text TEXT NOT NULL,
    tweet_url TEXT NOT NULL,
    media_urls JSONB NOT NULL DEFAULT '[]'::jsonb,
    public_metrics JSONB NOT NULL DEFAULT '{}'::jsonb,
    ocr_text TEXT,
    discord_delivered_at TIMESTAMPTZ,
    parsed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_leak_tweets_source_created
    ON leak_tweets(source_id, created_at DESC);

CREATE TABLE IF NOT EXISTS leak_signals (
    id BIGSERIAL PRIMARY KEY,
    tweet_id TEXT UNIQUE NOT NULL REFERENCES leak_tweets(tweet_id) ON DELETE CASCADE,
    source_id BIGINT NOT NULL REFERENCES leak_sources(id) ON DELETE CASCADE,
    player_name TEXT,
    rating INTEGER,
    card_type TEXT,
    promo_name TEXT,
    signal_kind TEXT NOT NULL DEFAULT 'leak',
    parse_method TEXT NOT NULL DEFAULT 'regex',
    parse_confidence NUMERIC(5,4) NOT NULL DEFAULT 0,
    reliability_score NUMERIC(5,4) NOT NULL DEFAULT 0,
    confidence_tag TEXT NOT NULL DEFAULT 'rumor'
        CHECK (confidence_tag IN ('high','medium','rumor')),
    matched_source_card_id BIGINT,
    matched_source_player_id BIGINT,
    confirmed BOOLEAN,
    confirmed_at TIMESTAMPTZ,
    confirmation_notes TEXT,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_leak_signals_recent
    ON leak_signals(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_leak_signals_player
    ON leak_signals(lower(player_name)) WHERE player_name IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_leak_signals_unconfirmed
    ON leak_signals(created_at DESC) WHERE confirmed IS NULL;

-- Discovery candidates are deliberately kept separate from core sources.
CREATE TABLE IF NOT EXISTS leak_discovery_candidates (
    id BIGSERIAL PRIMARY KEY,
    x_user_id TEXT,
    username TEXT,
    tweet_id TEXT UNIQUE NOT NULL,
    tweet_text TEXT NOT NULL,
    tweet_url TEXT NOT NULL,
    followers_count BIGINT,
    public_metrics JSONB NOT NULL DEFAULT '{}'::jsonb,
    candidate_score NUMERIC(5,4) NOT NULL DEFAULT 0,
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    promoted_at TIMESTAMPTZ
);

-- Internal-only helper view for Trade Finder / Opportunities.
-- It deliberately excludes tweet text, tweet URLs and media URLs so product
-- consumers never receive or display the source tweet.
CREATE OR REPLACE VIEW active_leak_signals AS
SELECT
    ls.id,
    ls.player_name,
    ls.rating,
    ls.card_type,
    ls.promo_name,
    ls.confidence_tag,
    ls.reliability_score,
    ls.parse_confidence,
    ls.matched_source_card_id,
    ls.matched_source_player_id,
    ls.created_at
FROM leak_signals ls
WHERE ls.created_at >= now() - interval '14 days'
  AND COALESCE(ls.confirmed, TRUE) IS TRUE;
