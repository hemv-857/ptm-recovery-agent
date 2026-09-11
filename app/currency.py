"""
Multi-currency normalization engine.
Supports USD, EUR, INR with real-time rate fetching and caching.
"""
import json
import logging
import os
import time
from decimal import ROUND_HALF_UP, Decimal

logger = logging.getLogger(__name__)

# ISO 4217 currency codes
CURRENCIES = {
    "INR": {"symbol": "₹", "name": "Indian Rupee", "decimals": 2},
    "USD": {"symbol": "$", "name": "US Dollar", "decimals": 2},
    "EUR": {"symbol": "€", "name": "Euro", "decimals": 2},
}

# Fallback rates (updated periodically via API)
DEFAULT_RATES = {
    "INR": 1.0,
    "USD": 0.012,  # 1 INR = 0.012 USD
    "EUR": 0.011,  # 1 INR = 0.011 EUR
}

class CurrencyNormalizer:
    """Real-time currency normalization with caching."""

    def __init__(self, base_currency: str = "INR"):
        self.base_currency = base_currency.upper()
        self._rates = DEFAULT_RATES.copy()
        self._rate_timestamp = 0
        self._cache_ttl = 3600  # 1 hour
        self._api_key = os.getenv("EXCHANGE_RATE_API_KEY")

    def _fetch_rates(self) -> bool:
        """Fetch live rates from exchangerate.host (free, no key needed)."""
        try:
            import urllib.request
            url = f"https://api.exchangerate.host/latest?base={self.base_currency}&symbols=USD,EUR"
            with urllib.request.urlopen(url, timeout=5) as resp:
                data = json.loads(resp.read().decode())
                if data.get("success") and "rates" in data:
                    self._rates["USD"] = data["rates"].get("USD", DEFAULT_RATES["USD"])
                    self._rates["EUR"] = data["rates"].get("EUR", DEFAULT_RATES["EUR"])
                    self._rate_timestamp = time.time()
                    usd = self._rates['USD']
                    eur = self._rates['EUR']
                    logger.info(f"Currency rates updated: USD={usd:.6f}, EUR={eur:.6f}")
                    return True
        except Exception as e:
            logger.warning(f"Failed to fetch currency rates: {e}")
        return False

    def _ensure_fresh_rates(self):
        if time.time() - self._rate_timestamp > self._cache_ttl:
            self._fetch_rates()

    def convert(self, amount_paise: int, from_currency: str, to_currency: str) -> int:
        """Convert amount from one currency to another (returns paise-equivalent)."""
        from_curr = from_currency.upper()
        to_curr = to_currency.upper()

        if from_curr == to_curr:
            return amount_paise

        self._ensure_fresh_rates()

        # Convert to base (INR) first, then to target
        amount_base = amount_paise / 100  # paise to rupees
        if from_curr != self.base_currency:
            rate = self._rates.get(from_curr, 1.0)
            if rate > 0:
                amount_base = amount_base / rate

        if to_curr != self.base_currency:
            rate = self._rates.get(to_curr, 1.0)
            amount_base = amount_base * rate

        # Return in target currency's smallest unit
        decimals = CURRENCIES.get(to_curr, {}).get("decimals", 2)
        multiplier = 10 ** decimals
        quantized = Decimal(str(amount_base * multiplier)).quantize(
            Decimal("1"), rounding=ROUND_HALF_UP
        )
        return int(quantized)

    def format(self, amount_paise: int, currency: str) -> str:
        """Format amount with currency symbol."""
        curr = currency.upper()
        info = CURRENCIES.get(curr, CURRENCIES["INR"])
        decimals = info["decimals"]
        amount = amount_paise / (10 ** decimals)
        return f"{info['symbol']}{amount:,.{decimals}f}"

    def get_rate(self, from_currency: str, to_currency: str) -> float:
        """Get exchange rate between two currencies."""
        self._ensure_fresh_rates()
        from_curr = from_currency.upper()
        to_curr = to_currency.upper()
        if from_curr == to_curr:
            return 1.0
        base_from = self._rates.get(from_curr, 1.0)
        base_to = self._rates.get(to_curr, 1.0)
        return base_to / base_from if base_from > 0 else 1.0


_normalizer = None

def get_normalizer() -> CurrencyNormalizer:
    global _normalizer
    if _normalizer is None:
        _normalizer = CurrencyNormalizer()
    return _normalizer
