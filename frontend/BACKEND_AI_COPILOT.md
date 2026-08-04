# AI Copilot Implementation Guide

Complete implementation guide for the AI Trading Copilot feature using OpenAI.

## Overview

The AI Copilot provides intelligent trading advice based on:
- Current market prices and trends
- Technical indicators (RSI, ATR)
- Historical performance
- Market context (events, weekend league, etc.)

## Implementation

### 1. Core Function

```python
# app/services/ai_copilot.py
import openai
import os
from datetime import datetime
from app.services.player_service import (
    get_player_metadata,
    get_price_history,
    get_current_price
)
from app.services.indicators import calculate_rsi, calculate_atr

openai.api_key = os.environ.get('OPENAI_API_KEY')

SYSTEM_PROMPT = """You are an expert FIFA Ultimate Team (FUT) trading advisor. Your role is to provide clear, actionable trading advice based on market data and technical analysis.

Key principles:
- Be concise and direct (2-3 sentences max)
- Provide specific price targets when possible
- Consider both technical indicators and market context
- Acknowledge uncertainty when data is limited
- Use trading terminology (buy zone, sell zone, resistance, support)
- Never guarantee profits - always mention risks

Response format:
1. Direct recommendation (Buy/Sell/Hold/Wait)
2. Key reasoning (1-2 factors)
3. Suggested action or price target
"""

async def get_trading_advice(card_id: str, platform: str, user_question: str = None):
    """
    Get AI trading advice for a specific player card.

    Args:
        card_id: Player card ID
        platform: Trading platform (ps/xbox/pc)
        user_question: Optional specific question from user

    Returns:
        dict: {
            'advice': str,
            'confidence': float,
            'key_factors': list[str],
            'price_targets': dict,
            'risk_level': str
        }
    """

    # Gather market data
    player = await get_player_metadata(card_id)
    current_price = await get_current_price(card_id, platform)
    history = await get_price_history(card_id, platform, timeframe='24h')

    # Calculate indicators
    rsi = calculate_rsi(history) if len(history) > 14 else None
    atr = calculate_atr(history) if len(history) > 1 else None

    # Build market context
    context = build_market_context(player, current_price, history, rsi, atr, platform)

    # Generate prompt
    user_prompt = f"""Player: {player['name']} ({player['rating']} {player['position']})
Platform: {platform.upper()}

MARKET DATA:
{context}

{"USER QUESTION: " + user_question if user_question else "Provide general trading advice for this player."}

Respond with:
1. Recommendation: [BUY/SELL/HOLD/WAIT]
2. Reasoning: [Key factors]
3. Action: [Specific advice]
"""

    # Call OpenAI
    try:
        response = await openai.ChatCompletion.acreate(
            model=os.environ.get('OPENAI_MODEL', 'gpt-4-turbo-preview'),
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt}
            ],
            max_tokens=int(os.environ.get('OPENAI_MAX_TOKENS', 500)),
            temperature=float(os.environ.get('OPENAI_TEMPERATURE', 0.7)),
        )

        advice_text = response.choices[0].message.content

        # Parse response and extract structured data
        parsed = parse_advice_response(advice_text, current_price, rsi)

        return {
            'advice': advice_text,
            'confidence': parsed['confidence'],
            'key_factors': parsed['factors'],
            'price_targets': parsed['targets'],
            'risk_level': parsed['risk'],
            'metadata': {
                'current_price': current_price,
                'rsi': rsi,
                'atr': atr,
                'model': response.model,
                'tokens_used': response.usage.total_tokens
            }
        }

    except Exception as e:
        # Fallback to rule-based advice if API fails
        return get_fallback_advice(current_price, rsi, atr)


def build_market_context(player, current_price, history, rsi, atr, platform):
    """Build rich context string for the AI"""

    if not history or len(history) == 0:
        return f"Current Price: {current_price:,} coins\nInsufficient historical data for analysis."

    # Calculate stats
    prices = [h['close'] for h in history]
    avg_price = sum(prices) / len(prices)
    min_price = min(prices)
    max_price = max(prices)
    volatility = (max_price - min_price) / avg_price * 100

    # Determine trend
    recent_prices = prices[-10:]
    if len(recent_prices) >= 2:
        trend = "rising" if recent_prices[-1] > recent_prices[0] else "falling"
        trend_strength = abs(recent_prices[-1] - recent_prices[0]) / recent_prices[0] * 100
    else:
        trend = "stable"
        trend_strength = 0

    # Check if price is in buy/sell zone
    cheap_zone = avg_price * 0.97
    expensive_zone = avg_price * 1.03

    if current_price < cheap_zone:
        zone = "BUY ZONE (below average)"
    elif current_price > expensive_zone:
        zone = "SELL ZONE (above average)"
    else:
        zone = "NEUTRAL ZONE (near average)"

    # Market timing context
    context_lines = [
        f"Current Price: {current_price:,} coins",
        f"24h Average: {int(avg_price):,} coins",
        f"24h Range: {min_price:,} - {max_price:,} coins",
        f"Volatility: {volatility:.1f}%",
        f"Trend: {trend.upper()} ({trend_strength:.1f}%)",
        f"Zone: {zone}",
    ]

    if rsi is not None:
        if rsi < 30:
            context_lines.append(f"RSI: {rsi:.1f} (OVERSOLD - potential buy)")
        elif rsi > 70:
            context_lines.append(f"RSI: {rsi:.1f} (OVERBOUGHT - potential sell)")
        else:
            context_lines.append(f"RSI: {rsi:.1f} (neutral)")

    if atr is not None:
        context_lines.append(f"Average True Range: {int(atr):,} coins")

    # Add time-based context
    now = datetime.now()
    day_name = now.strftime("%A")
    hour = now.hour

    timing_context = []
    if day_name in ["Thursday", "Friday"]:
        timing_context.append("Weekend League starts Friday - expect increased demand")
    elif day_name == "Monday":
        timing_context.append("Monday after Weekend League - expect sell-off pressure")

    if 18 <= hour <= 21:
        timing_context.append("Peak trading hours (6-9 PM)")
    elif 2 <= hour <= 6:
        timing_context.append("Low activity hours (2-6 AM)")

    if timing_context:
        context_lines.append(f"\nMarket Timing: {', '.join(timing_context)}")

    return '\n'.join(context_lines)


def parse_advice_response(advice_text, current_price, rsi):
    """Extract structured data from AI response"""

    advice_lower = advice_text.lower()

    # Determine confidence based on keywords
    confidence = 0.7  # default
    if any(word in advice_lower for word in ['strong', 'definitely', 'highly', 'clear']):
        confidence = 0.9
    elif any(word in advice_lower for word in ['might', 'possibly', 'uncertain', 'unclear']):
        confidence = 0.5

    # Extract key factors
    factors = []
    if 'rsi' in advice_lower or 'oversold' in advice_lower or 'overbought' in advice_lower:
        factors.append('RSI indicator')
    if 'trend' in advice_lower or 'rising' in advice_lower or 'falling' in advice_lower:
        factors.append('Price trend')
    if 'weekend' in advice_lower:
        factors.append('Weekend League timing')
    if 'average' in advice_lower or 'zone' in advice_lower:
        factors.append('Price zones')

    # Determine risk level
    risk = 'medium'
    if rsi and (rsi < 25 or rsi > 75):
        risk = 'high'
    elif rsi and (35 <= rsi <= 65):
        risk = 'low'

    # Extract price targets (simple regex)
    import re
    price_mentions = re.findall(r'(\d{1,3}[,\d]*)\s*(?:coins?|k)', advice_text)
    targets = {}
    if price_mentions:
        # First mention often buy target, second sell target
        if len(price_mentions) >= 1:
            targets['buy_below'] = parse_price(price_mentions[0])
        if len(price_mentions) >= 2:
            targets['sell_above'] = parse_price(price_mentions[-1])

    return {
        'confidence': confidence,
        'factors': factors if factors else ['Market analysis'],
        'targets': targets,
        'risk': risk
    }


def parse_price(price_str):
    """Convert price string to integer"""
    price_str = price_str.replace(',', '').replace('k', '000')
    try:
        return int(price_str)
    except:
        return None


def get_fallback_advice(current_price, rsi, atr):
    """Rule-based fallback when OpenAI fails"""

    advice = "Unable to get AI advice. Here's a basic analysis:\n\n"

    if rsi is not None:
        if rsi < 30:
            advice += "RSI indicates oversold conditions - potential buying opportunity. "
        elif rsi > 70:
            advice += "RSI indicates overbought conditions - consider selling. "
        else:
            advice += "RSI is neutral. "

    advice += "Monitor the market and use your best judgment."

    return {
        'advice': advice,
        'confidence': 0.3,
        'key_factors': ['Basic indicator analysis'],
        'price_targets': {},
        'risk_level': 'unknown',
        'fallback': True
    }
```

### 2. API Endpoint

```python
# app/routes/ai.py
from flask import Blueprint, request, jsonify
from app.decorators import require_auth, require_feature
from app.services.ai_copilot import get_trading_advice
import asyncio

bp = Blueprint('ai', __name__)

@bp.route('/api/ai/copilot', methods=['POST'])
@require_auth
@require_feature('ai_copilot')  # Pro tier required
async def copilot():
    """
    Get AI trading advice for a player

    Request body:
    {
        "card_id": "266111",
        "platform": "ps",
        "user_question": "Should I buy now?" (optional)
    }
    """
    data = request.get_json()

    card_id = data.get('card_id')
    platform = data.get('platform', 'ps')
    user_question = data.get('user_question')

    if not card_id:
        return jsonify({'error': 'card_id required'}), 400

    try:
        result = await get_trading_advice(card_id, platform, user_question)
        return jsonify(result), 200

    except Exception as e:
        return jsonify({
            'error': 'Failed to get advice',
            'message': str(e)
        }), 500
```

### 3. Rate Limiting for AI Copilot

```python
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

limiter = Limiter(
    app,
    key_func=lambda: get_current_user_id(),  # Rate limit per user
    default_limits=[]
)

# Apply stricter limits to AI endpoint (expensive)
@bp.route('/api/ai/copilot', methods=['POST'])
@require_auth
@require_feature('ai_copilot')
@limiter.limit("10/hour")  # Only 10 AI queries per hour for Pro
@limiter.limit("30/hour", override_defaults=False, deduct_when=lambda r: r.user.tier == 'elite')  # 30/hour for Elite
async def copilot():
    # ...
```

### 4. Caching Responses

```python
from functools import lru_cache
from datetime import datetime, timedelta
import hashlib

# Simple in-memory cache
cache = {}

def cache_key(card_id, platform, question):
    """Generate cache key"""
    q_hash = hashlib.md5((question or '').encode()).hexdigest()[:8]
    return f"copilot:{card_id}:{platform}:{q_hash}"

async def get_trading_advice(card_id, platform, user_question=None):
    # Check cache (5 minute TTL)
    key = cache_key(card_id, platform, user_question)
    cached = cache.get(key)

    if cached and cached['expires'] > datetime.now():
        cached['cached'] = True
        return cached['data']

    # Generate new advice
    result = await _generate_advice(card_id, platform, user_question)

    # Cache it
    cache[key] = {
        'data': result,
        'expires': datetime.now() + timedelta(minutes=5)
    }

    return result
```

## Example Prompts & Responses

### Example 1: General Advice

**Input:**
```json
{
  "card_id": "266111",
  "platform": "ps"
}
```

**AI Response:**
```
Recommendation: BUY

This card is currently in the buy zone at 44,500 coins (3% below 24h average). RSI at 28 indicates oversold conditions, suggesting an upward correction is likely. With Weekend League starting Friday, demand should increase.

Action: Buy now if under 45k, target sell at 48-50k Friday evening.
```

### Example 2: Specific Question

**Input:**
```json
{
  "card_id": "183277",
  "platform": "ps",
  "user_question": "I bought at 120k, should I sell now at 115k?"
}
```

**AI Response:**
```
Recommendation: HOLD

Despite being down 5k, selling now locks in a loss. Current price of 115k is near support (24h low: 114k). RSI at 35 suggests oversold, not panic territory. Monday sell-off pressure is common but temporary.

Action: Hold through Tuesday. If price recovers to 118k+, consider selling. Set a stop-loss at 112k to limit further downside.
```

### Example 3: High Volatility Warning

**Input:**
```json
{
  "card_id": "231443",
  "platform": "xbox",
  "user_question": "Good investment?"
}
```

**AI Response:**
```
Recommendation: WAIT

This card shows extreme volatility (40% range in 24h) with no clear trend. Current price at 89k is mid-range but unstable. Without clearer support/resistance levels, risk is high.

Action: Wait for price to stabilize or drop to 24h low (82k) before considering entry. High risk = high potential reward, but not recommended for conservative trading.
```

## Prompt Engineering Tips

### 1. Use Few-Shot Examples

```python
SYSTEM_PROMPT = """You are a FUT trading expert.

Example responses:
- "BUY - Price 3% below avg, RSI oversold (28). Target: 48k by Friday."
- "SELL - Overbought (RSI 76), weekend league ending. Sell before Monday crash."
- "HOLD - Neutral indicators, low volatility. Wait for clearer signal."

Be concise, actionable, and always include reasoning.
"""
```

### 2. Constrain Output Format

```python
user_prompt = f"""...

Respond in EXACTLY this format:
RECOMMENDATION: [BUY/SELL/HOLD/WAIT]
CONFIDENCE: [0-100]%
KEY FACTORS:
- [Factor 1]
- [Factor 2]
ACTION: [Specific next step]
"""
```

### 3. Handle Edge Cases

```python
if len(history) < 10:
    context += "\n\nWARNING: Limited data available. Provide cautious advice with low confidence."

if volatility > 30:
    context += "\n\nNOTE: High volatility detected. Warn user about increased risk."
```

## Cost Optimization

### Estimated Costs (GPT-4)
- Input: ~200 tokens per request
- Output: ~150 tokens per request
- Cost: ~$0.01 per request (GPT-4)
- Cost: ~$0.001 per request (GPT-3.5-turbo)

### Strategies to Reduce Costs

1. **Use GPT-3.5-turbo for simple queries**
   ```python
   model = 'gpt-4' if user.tier == 'elite' else 'gpt-3.5-turbo'
   ```

2. **Implement aggressive caching**
   - 5 min cache for identical queries
   - 15 min cache for same player (different questions)

3. **Batch requests** (if applicable)
   ```python
   # Process multiple players in one API call
   ```

4. **Fallback to rules when appropriate**
   ```python
   if rsi and atr and len(history) > 20:
       # Sufficient data for rule-based advice
       return get_rule_based_advice(...)
   else:
       # Use AI for complex cases
       return get_ai_advice(...)
   ```

## Monitoring & Logging

```python
import logging
from app.utils.metrics import track_metric

logger = logging.getLogger(__name__)

async def get_trading_advice(...):
    start = time.time()

    try:
        result = await _generate_advice(...)

        # Log success metrics
        duration = time.time() - start
        track_metric('ai_copilot_request', {
            'status': 'success',
            'duration': duration,
            'tokens': result['metadata']['tokens_used'],
            'model': result['metadata']['model']
        })

        logger.info(f"AI advice generated for {card_id} in {duration:.2f}s")

        return result

    except openai.error.RateLimitError:
        logger.warning("OpenAI rate limit hit")
        return get_fallback_advice(...)

    except Exception as e:
        logger.error(f"AI copilot error: {e}")
        track_metric('ai_copilot_request', {'status': 'error'})
        raise
```

## Testing

```python
import pytest
from app.services.ai_copilot import get_trading_advice

@pytest.mark.asyncio
async def test_copilot_buy_recommendation():
    """Test that copilot recommends buy when price is low"""
    # Mock data showing low price + oversold RSI
    result = await get_trading_advice(
        card_id='test_card',
        platform='ps'
    )

    assert 'advice' in result
    assert result['confidence'] > 0.5
    assert 'BUY' in result['advice'].upper() or 'buy' in result['advice'].lower()


@pytest.mark.asyncio
async def test_copilot_handles_missing_data():
    """Test graceful fallback with insufficient data"""
    result = await get_trading_advice(
        card_id='no_data_card',
        platform='ps'
    )

    assert result['confidence'] < 0.5  # Low confidence
    assert 'insufficient' in result['advice'].lower() or result.get('fallback') is True
```

## Frontend Integration

The frontend already has the UI built. Just ensure your endpoint matches:

```javascript
// Frontend is calling:
POST /api/ai/copilot
{
  "card_id": "266111",
  "platform": "ps",
  "user_question": "Should I buy?"
}

// Expects response:
{
  "advice": "BUY - Price below average...",
  "confidence": 0.85,
  "key_factors": ["RSI oversold", "Weekend League timing"],
  "price_targets": { "buy_below": 45000, "sell_above": 50000 },
  "risk_level": "medium"
}
```

---

**Ready to implement!** All frontend code for AI Copilot is complete and waiting for this backend endpoint. 🚀
