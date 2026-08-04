# Backend Environment Variables Template

Copy this to your backend `.env` file and fill in the values.

```bash
# =============================================================================
# Database Configuration
# =============================================================================
DATABASE_URL=postgresql://user:password@localhost:5432/futhub_prod
DATABASE_POOL_SIZE=20
DATABASE_MAX_OVERFLOW=10

# =============================================================================
# Application Settings
# =============================================================================
FLASK_ENV=production
SECRET_KEY=your-secret-key-here-change-in-production
API_BASE_URL=https://api.futhub.co.uk

# =============================================================================
# CORS Settings
# =============================================================================
ALLOWED_ORIGINS=https://futhub.co.uk,https://www.futhub.co.uk
CORS_CREDENTIALS=true

# =============================================================================
# Authentication
# =============================================================================
JWT_SECRET_KEY=your-jwt-secret-key-here
JWT_ACCESS_TOKEN_EXPIRES=3600  # 1 hour in seconds
SESSION_COOKIE_SECURE=true
SESSION_COOKIE_HTTPONLY=true
SESSION_COOKIE_SAMESITE=lax

# =============================================================================
# Stripe Configuration
# =============================================================================
STRIPE_SECRET_KEY=sk_live_...
STRIPE_PUBLISHABLE_KEY=pk_live_...
STRIPE_WEBHOOK_SECRET=whsec_...

# Stripe Product/Price IDs
STRIPE_BASIC_MONTHLY_PRICE_ID=price_...
STRIPE_BASIC_YEARLY_PRICE_ID=price_...
STRIPE_PRO_MONTHLY_PRICE_ID=price_...
STRIPE_PRO_YEARLY_PRICE_ID=price_...
STRIPE_ELITE_MONTHLY_PRICE_ID=price_...
STRIPE_ELITE_YEARLY_PRICE_ID=price_...

# =============================================================================
# OpenAI Configuration (for AI Copilot)
# =============================================================================
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-4-turbo-preview
OPENAI_MAX_TOKENS=500
OPENAI_TEMPERATURE=0.7

# =============================================================================
# Rate Limiting
# =============================================================================
RATE_LIMIT_ENABLED=true

# Free tier: 100 requests/hour
RATE_LIMIT_FREE_TIER=100/hour

# Basic tier: 500 requests/hour
RATE_LIMIT_BASIC_TIER=500/hour

# Pro tier: 1000 requests/hour
RATE_LIMIT_PRO_TIER=1000/hour

# Elite tier: 5000 requests/hour
RATE_LIMIT_ELITE_TIER=5000/hour

# =============================================================================
# Redis (for caching & rate limiting)
# =============================================================================
REDIS_URL=redis://localhost:6379/0
CACHE_TTL=300  # 5 minutes default

# =============================================================================
# Email Configuration (for receipts, notifications)
# =============================================================================
SMTP_HOST=smtp.sendgrid.net
SMTP_PORT=587
SMTP_USER=apikey
SMTP_PASSWORD=your-sendgrid-api-key
FROM_EMAIL=noreply@futhub.co.uk
SUPPORT_EMAIL=support@futhub.co.uk

# =============================================================================
# External APIs
# =============================================================================
# FUT API credentials (if using third-party data)
FUT_API_KEY=your-fut-api-key
FUT_API_BASE=https://futdb.app/api

# =============================================================================
# Monitoring & Logging
# =============================================================================
SENTRY_DSN=https://...@sentry.io/...
LOG_LEVEL=INFO
ENABLE_REQUEST_LOGGING=true

# =============================================================================
# Feature Flags
# =============================================================================
FEATURE_AI_COPILOT_ENABLED=true
FEATURE_SENTIMENT_ENABLED=true
FEATURE_LEADERBOARD_ENABLED=true
FEATURE_REFERRALS_ENABLED=true

# =============================================================================
# Referral Rewards
# =============================================================================
REFERRAL_REWARD_DAYS=7  # Grant 7 days of premium per successful referral
MIN_REFERRED_TRADES=10  # Minimum trades for referral to count as "completed"

# =============================================================================
# Cron Jobs
# =============================================================================
ENABLE_CRON_JOBS=true
CRON_CLEANUP_EXPIRED_SUBSCRIPTIONS=0 2 * * *  # Daily at 2 AM
CRON_UPDATE_LEADERBOARD=*/15 * * * *          # Every 15 minutes
CRON_AGGREGATE_SENTIMENT=*/5 * * * *          # Every 5 minutes
```

## Development vs Production

### Development `.env`
```bash
FLASK_ENV=development
DATABASE_URL=postgresql://localhost/futhub_dev
STRIPE_SECRET_KEY=sk_test_...
OPENAI_API_KEY=sk-...  # Use lower rate limit key for dev
ALLOWED_ORIGINS=http://localhost:5173,http://localhost:3000
SESSION_COOKIE_SECURE=false  # Allow HTTP in dev
LOG_LEVEL=DEBUG
```

### Production `.env`
```bash
FLASK_ENV=production
DATABASE_URL=postgresql://prod-db/futhub_prod
STRIPE_SECRET_KEY=sk_live_...
OPENAI_API_KEY=sk-...  # Use production key
ALLOWED_ORIGINS=https://futhub.co.uk,https://www.futhub.co.uk
SESSION_COOKIE_SECURE=true  # Require HTTPS
LOG_LEVEL=INFO
```

## Security Checklist

- [ ] Change all default secret keys
- [ ] Use strong DATABASE_PASSWORD
- [ ] Never commit `.env` to git (add to `.gitignore`)
- [ ] Rotate STRIPE_WEBHOOK_SECRET regularly
- [ ] Use environment-specific Stripe keys (test vs live)
- [ ] Enable SSL/TLS for database connections in production
- [ ] Restrict ALLOWED_ORIGINS to production domains only
- [ ] Set SESSION_COOKIE_SECURE=true in production
- [ ] Use Redis password if exposed to internet
- [ ] Configure firewall rules for database/Redis access
