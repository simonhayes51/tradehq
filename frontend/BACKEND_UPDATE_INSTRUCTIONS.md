# Backend Implementation Guide for Monetization Features

This document outlines all backend changes needed to support the new monetization and premium features in the FutHub frontend.

## Table of Contents
1. [Database Schema Updates](#database-schema-updates)
2. [New API Endpoints](#new-api-endpoints)
3. [Existing Endpoint Updates](#existing-endpoint-updates)
4. [Entitlements System](#entitlements-system)
5. [Testing Checklist](#testing-checklist)
6. [Deployment Steps](#deployment-steps)

---

## 1. Database Schema Updates

### Multi-Tier Premium System

Create migration to add tier-based premium system:

```sql
-- Add premium tier column to users table
ALTER TABLE users ADD COLUMN premium_tier VARCHAR(20) DEFAULT 'free';
ALTER TABLE users ADD COLUMN premium_expires_at TIMESTAMP NULL;
ALTER TABLE users ADD COLUMN stripe_subscription_id VARCHAR(255) NULL;

-- Create entitlements table
CREATE TABLE user_entitlements (
  id SERIAL PRIMARY KEY,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  feature_key VARCHAR(50) NOT NULL,
  granted_at TIMESTAMP DEFAULT NOW(),
  expires_at TIMESTAMP NULL,
  UNIQUE(user_id, feature_key)
);

CREATE INDEX idx_user_entitlements_user_id ON user_entitlements(user_id);
CREATE INDEX idx_user_entitlements_feature ON user_entitlements(feature_key);

-- Create portfolio table
CREATE TABLE user_portfolios (
  id SERIAL PRIMARY KEY,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  card_id VARCHAR(50) NOT NULL,
  quantity INTEGER DEFAULT 1,
  purchase_price INTEGER NOT NULL,
  purchase_platform VARCHAR(10) NOT NULL,
  purchased_at TIMESTAMP DEFAULT NOW(),
  notes TEXT,
  created_at TIMESTAMP DEFAULT NOW(),
  updated_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_portfolios_user_id ON user_portfolios(user_id);
CREATE INDEX idx_portfolios_card_id ON user_portfolios(card_id);

-- Create referrals table
CREATE TABLE referrals (
  id SERIAL PRIMARY KEY,
  referrer_user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  referred_user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  status VARCHAR(20) DEFAULT 'pending', -- pending, completed, rewarded
  reward_granted BOOLEAN DEFAULT FALSE,
  created_at TIMESTAMP DEFAULT NOW(),
  completed_at TIMESTAMP NULL,
  UNIQUE(referred_user_id)
);

CREATE INDEX idx_referrals_referrer ON referrals(referrer_user_id);
CREATE INDEX idx_referrals_referred ON referrals(referred_user_id);

-- Create sentiment tracking table
CREATE TABLE card_sentiment (
  id SERIAL PRIMARY KEY,
  card_id VARCHAR(50) NOT NULL,
  platform VARCHAR(10) NOT NULL,
  sentiment_score DECIMAL(3,2) DEFAULT 0, -- -1 to +1
  bullish_count INTEGER DEFAULT 0,
  bearish_count INTEGER DEFAULT 0,
  updated_at TIMESTAMP DEFAULT NOW(),
  UNIQUE(card_id, platform)
);

CREATE INDEX idx_sentiment_card ON card_sentiment(card_id);
```

---

## 2. New API Endpoints

### 2.1 Portfolio Management

#### GET `/api/portfolio`
**Description:** Get user's portfolio
**Auth:** Required
**Response:**
```json
{
  "items": [
    {
      "id": 123,
      "card_id": "266111",
      "player_name": "Mohamed Salah",
      "quantity": 3,
      "purchase_price": 45000,
      "current_price": 48000,
      "profit_loss": 9000,
      "profit_loss_pct": 6.67,
      "purchase_platform": "ps",
      "purchased_at": "2025-12-01T10:30:00Z",
      "notes": "Bought before weekend league"
    }
  ],
  "total_value": 144000,
  "total_profit_loss": 9000
}
```

#### POST `/api/portfolio`
**Description:** Add item to portfolio
**Auth:** Required
**Body:**
```json
{
  "card_id": "266111",
  "quantity": 2,
  "purchase_price": 45000,
  "purchase_platform": "ps",
  "notes": "Optional notes"
}
```

#### DELETE `/api/portfolio/:id`
**Description:** Remove item from portfolio
**Auth:** Required

---

### 2.2 AI Copilot

#### POST `/api/ai/copilot`
**Description:** Get AI trading advice
**Auth:** Required (Premium feature)
**Body:**
```json
{
  "card_id": "266111",
  "platform": "ps",
  "user_question": "Should I buy now?"
}
```
**Response:**
```json
{
  "advice": "Based on current market conditions...",
  "confidence": 0.85,
  "key_factors": [
    "Price is in buy zone (3% below avg)",
    "RSI indicates oversold",
    "Weekend league starts tomorrow"
  ]
}
```

**Implementation:** Use OpenAI GPT-4 with market context:
```python
def get_copilot_advice(card_id, platform, user_question):
    # Get market data
    price_data = get_price_history(card_id, platform)
    current_price = get_current_price(card_id, platform)

    # Build context
    context = f"""
    Player: {get_player_name(card_id)}
    Current Price: {current_price}
    24h Avg: {price_data['avg']}
    RSI: {calculate_rsi(price_data)}
    Market Trend: {analyze_trend(price_data)}
    """

    # Call OpenAI
    response = openai.ChatCompletion.create(
        model="gpt-4",
        messages=[
            {"role": "system", "content": "You are a FIFA Ultimate Team trading expert..."},
            {"role": "user", "content": f"{context}\n\nQuestion: {user_question}"}
        ]
    )

    return response.choices[0].message.content
```

---

### 2.3 Sentiment Tracking

#### GET `/api/players/:card_id/sentiment`
**Description:** Get community sentiment for a player
**Auth:** Optional
**Response:**
```json
{
  "card_id": "266111",
  "platform": "ps",
  "sentiment": "bullish",
  "score": 0.65,
  "bullish_count": 450,
  "bearish_count": 200,
  "trend": "rising",
  "updated_at": "2025-12-07T10:00:00Z"
}
```

#### POST `/api/players/:card_id/sentiment`
**Description:** Submit sentiment vote
**Auth:** Required
**Body:**
```json
{
  "platform": "ps",
  "vote": "bullish" // or "bearish"
}
```

---

### 2.4 Leaderboard

#### GET `/api/leaderboard`
**Description:** Get top traders leaderboard
**Auth:** Optional
**Query params:**
- `timeframe`: "week" | "month" | "alltime" (default: "week")
- `limit`: number (default: 100)

**Response:**
```json
{
  "timeframe": "week",
  "leaders": [
    {
      "rank": 1,
      "username": "TraderPro123",
      "profit": 2500000,
      "trades": 450,
      "win_rate": 0.68,
      "badge": "elite"
    }
  ],
  "user_rank": {
    "rank": 42,
    "profit": 150000,
    "trades": 89,
    "win_rate": 0.58
  }
}
```

---

### 2.5 Referral System

#### GET `/api/referrals/code`
**Description:** Get user's referral code
**Auth:** Required
**Response:**
```json
{
  "code": "TRADER123",
  "url": "https://futhub.co.uk/signup?ref=TRADER123",
  "rewards": {
    "per_referral": "1 week premium",
    "referrals_count": 3,
    "pending_count": 1,
    "total_rewards": "3 weeks premium"
  }
}
```

#### GET `/api/referrals`
**Description:** Get user's referral history
**Auth:** Required
**Response:**
```json
{
  "referrals": [
    {
      "id": 1,
      "username": "NewUser123",
      "status": "completed",
      "reward_granted": true,
      "created_at": "2025-12-01T10:00:00Z",
      "completed_at": "2025-12-05T15:30:00Z"
    }
  ]
}
```

---

### 2.6 Bulk Trade Management

#### POST `/api/trades/bulk`
**Description:** Record multiple trades at once
**Auth:** Required
**Body:**
```json
{
  "trades": [
    {
      "card_id": "266111",
      "buy_price": 45000,
      "sell_price": 48000,
      "quantity": 2,
      "platform": "ps",
      "notes": "Weekend league flip"
    }
  ]
}
```

---

### 2.7 Enhanced Billing

#### GET `/api/billing/plans`
**Description:** Get available subscription plans
**Auth:** Optional
**Response:**
```json
{
  "plans": [
    {
      "id": "basic_monthly",
      "name": "Basic",
      "price": 4.99,
      "currency": "USD",
      "interval": "month",
      "features": [
        "smart_buy",
        "basic_analytics"
      ]
    },
    {
      "id": "pro_monthly",
      "name": "Pro",
      "price": 9.99,
      "currency": "USD",
      "interval": "month",
      "features": [
        "smart_buy",
        "trade_finder",
        "deal_confidence",
        "advanced_analytics",
        "ai_copilot"
      ],
      "popular": true
    },
    {
      "id": "elite_monthly",
      "name": "Elite",
      "price": 19.99,
      "currency": "USD",
      "interval": "month",
      "features": [
        "smart_buy",
        "trade_finder",
        "deal_confidence",
        "backtest",
        "smart_trending",
        "advanced_analytics",
        "ai_copilot",
        "priority_support"
      ]
    }
  ]
}
```

#### POST `/api/billing/checkout`
**Description:** Create Stripe checkout session
**Auth:** Required
**Body:**
```json
{
  "plan_id": "pro_monthly"
}
```
**Response:**
```json
{
  "checkout_url": "https://checkout.stripe.com/...",
  "session_id": "cs_test_..."
}
```

#### POST `/api/billing/portal`
**Description:** Get Stripe customer portal link
**Auth:** Required
**Response:**
```json
{
  "portal_url": "https://billing.stripe.com/p/session/..."
}
```

---

## 3. Existing Endpoint Updates

### 3.1 Fix Autocomplete (CRITICAL)

#### GET `/api/players/autocomplete`
**Current issue:** Returns items with `label` field
**Required fix:** Return items with `name` field

**Before:**
```json
{
  "items": [
    { "card_id": "266111", "label": "Mohamed Salah", "rating": 91 }
  ]
}
```

**After:**
```json
{
  "items": [
    {
      "card_id": "266111",
      "name": "Mohamed Salah",
      "rating": 91,
      "image_url": "https://...",
      "position": "RW"
    }
  ]
}
```

---

### 3.2 Trades Pagination

#### GET `/api/trades`
**Add support for:**
- `limit` query param (default: 50, max: 200)
- `offset` query param (default: 0)
- Return total count

**Response:**
```json
{
  "trades": [...],
  "total": 1250,
  "limit": 50,
  "offset": 0
}
```

---

## 4. Entitlements System

### Feature Keys
```python
FEATURE_KEYS = {
    'smart_buy': 'Smart Buy AI',
    'trade_finder': 'Trade Finder',
    'deal_confidence': 'Deal Confidence Score',
    'backtest': 'Backtest Strategies',
    'smart_trending': 'Smart Trending',
    'ai_copilot': 'AI Trading Copilot',
    'advanced_analytics': 'Advanced Analytics',
    'portfolio_tracking': 'Portfolio Tracking'
}
```

### Tier Mappings
```python
TIER_FEATURES = {
    'free': [],
    'basic': ['smart_buy'],
    'pro': ['smart_buy', 'trade_finder', 'deal_confidence', 'ai_copilot', 'advanced_analytics'],
    'elite': ['smart_buy', 'trade_finder', 'deal_confidence', 'backtest', 'smart_trending', 'ai_copilot', 'advanced_analytics']
}
```

### Endpoint: GET `/api/entitlements`
**Auth:** Required
**Response:**
```json
{
  "is_premium": true,
  "tier": "pro",
  "expires_at": "2026-01-07T10:00:00Z",
  "features": [
    "smart_buy",
    "trade_finder",
    "deal_confidence",
    "ai_copilot",
    "advanced_analytics"
  ],
  "limits": {
    "api_calls_per_hour": 1000,
    "portfolio_items": 100
  }
}
```

### Helper Functions
```python
def get_user_entitlements(user_id):
    """Get all features user has access to"""
    user = get_user(user_id)

    # Check if premium expired
    if user.premium_expires_at and user.premium_expires_at < datetime.now():
        user.premium_tier = 'free'
        db.commit()

    # Get tier features
    features = TIER_FEATURES.get(user.premium_tier, [])

    # Add any custom entitlements
    custom = db.query(UserEntitlement).filter_by(user_id=user_id).all()
    for ent in custom:
        if not ent.expires_at or ent.expires_at > datetime.now():
            if ent.feature_key not in features:
                features.append(ent.feature_key)

    return {
        'is_premium': user.premium_tier != 'free',
        'tier': user.premium_tier,
        'expires_at': user.premium_expires_at,
        'features': features
    }

def require_feature(feature_key):
    """Decorator to protect endpoints"""
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            user_id = get_current_user_id()
            entitlements = get_user_entitlements(user_id)

            if feature_key not in entitlements['features']:
                return jsonify({
                    'error': 'Premium feature required',
                    'feature': feature_key,
                    'required_tier': get_min_tier_for_feature(feature_key)
                }), 403

            return f(*args, **kwargs)
        return wrapper
    return decorator

# Usage example:
@app.route('/api/ai/copilot', methods=['POST'])
@require_auth
@require_feature('ai_copilot')
def copilot():
    # ...
```

---

## 5. Testing Checklist

### Database Migrations
- [ ] Run migrations on dev database
- [ ] Verify all indexes created
- [ ] Test rollback scripts
- [ ] Check foreign key constraints

### API Endpoints
- [ ] Test all new endpoints with Postman/curl
- [ ] Verify authentication works
- [ ] Test premium feature gates
- [ ] Check error responses (400, 401, 403, 500)
- [ ] Validate request/response schemas
- [ ] Test rate limiting

### Entitlements
- [ ] Free tier: Can't access premium features
- [ ] Basic tier: Can access smart_buy only
- [ ] Pro tier: Can access all Pro features
- [ ] Elite tier: Can access all features
- [ ] Expired subscription: Downgrade to free
- [ ] Custom entitlements: Override tier features

### Integration Tests
- [ ] Stripe checkout flow (sandbox)
- [ ] Stripe webhooks (subscription.created, subscription.deleted)
- [ ] Customer portal redirect
- [ ] Referral reward granting
- [ ] Portfolio profit/loss calculations
- [ ] AI Copilot responses

### Performance
- [ ] Portfolio endpoint < 500ms for 100 items
- [ ] Leaderboard endpoint < 1s for 1000 users
- [ ] Sentiment endpoint cached (5min TTL)
- [ ] Autocomplete < 200ms

---

## 6. Deployment Steps

### Pre-deployment
1. **Backup production database**
   ```bash
   pg_dump futhub_prod > backup_$(date +%Y%m%d).sql
   ```

2. **Set environment variables**
   ```bash
   OPENAI_API_KEY=sk-...
   STRIPE_SECRET_KEY=sk_live_...
   STRIPE_WEBHOOK_SECRET=whsec_...
   ```

3. **Update Stripe product catalog**
   - Create Basic, Pro, Elite products
   - Set up monthly/yearly prices
   - Configure webhook endpoints

### Deployment
1. **Run migrations**
   ```bash
   python manage.py db upgrade
   ```

2. **Deploy backend code**
   ```bash
   git pull origin main
   pip install -r requirements.txt
   systemctl restart futhub-api
   ```

3. **Verify health checks**
   ```bash
   curl https://api.futhub.co.uk/health
   ```

4. **Test critical paths**
   - [ ] User can sign up
   - [ ] Free user can access basic features
   - [ ] Premium checkout works
   - [ ] Premium user can access Pro features
   - [ ] Entitlements endpoint returns correct data

### Post-deployment
1. **Monitor logs**
   ```bash
   tail -f /var/log/futhub/api.log
   ```

2. **Check error rates**
   - Sentry dashboard
   - CloudWatch metrics
   - Response time p95

3. **Gradual rollout**
   - Enable for 10% of users
   - Monitor for 1 hour
   - Increase to 50%
   - Monitor for 1 hour
   - Full rollout

### Rollback Plan
If issues occur:
```bash
# 1. Revert code
git revert HEAD
systemctl restart futhub-api

# 2. Rollback migrations (if needed)
python manage.py db downgrade

# 3. Restore database (worst case)
psql futhub_prod < backup_YYYYMMDD.sql
```

---

## 7. Frontend Integration Notes

The frontend is already built and ready. Once backend is deployed:

1. **Update API_BASE** in frontend `.env`:
   ```
   VITE_API_URL=https://api.futhub.co.uk
   ```

2. **Verify CORS** allows frontend domain:
   ```python
   CORS(app, origins=[
       'https://futhub.co.uk',
       'https://www.futhub.co.uk'
   ], supports_credentials=True)
   ```

3. **Test authentication** flow:
   - Frontend sends credentials: include
   - Backend sets httpOnly cookie
   - Subsequent requests include cookie

---

## 8. Support & Monitoring

### Key Metrics to Track
- Premium conversion rate
- Churn rate
- Feature usage (which premium features are most used)
- API error rates per endpoint
- Response times p50, p95, p99
- Stripe payment success rate

### Alerts to Set Up
- Entitlements endpoint error rate > 1%
- Checkout session creation failures
- Webhook processing failures
- Database migration failures
- AI Copilot timeout > 10s

---

## Questions?

Contact the frontend team or create an issue in the repo. All frontend code is complete and ready for backend integration! 🚀
