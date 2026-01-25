# Options Trading Manual - Strangle Selling Strategy

A beginner-friendly guide to understanding the options selling strategy used in this application.

---

## Table of Contents

1. [What are Options?](#what-are-options)
2. [Call vs Put Options](#call-vs-put-options)
3. [Buying vs Selling Options](#buying-vs-selling-options)
4. [What is a Strangle?](#what-is-a-strangle)
5. [Why Sell Strangles?](#why-sell-strangles)
6. [Understanding Delta](#understanding-delta)
7. [Why 7-Delta Strikes?](#why-7-delta-strikes)
8. [The 14-Day to 7-Day Cycle](#the-14-day-to-7-day-cycle)
9. [What is VWAP?](#what-is-vwap)
10. [Entry Signal: Straddle Price > VWAP](#entry-signal-straddle-price--vwap)
11. [Why We Need the Move Functionality](#why-we-need-the-move-functionality)
12. [Putting It All Together](#putting-it-all-together)

---

## What are Options?

An **option** is a contract that gives you the right (but not obligation) to buy or sell something at a fixed price before a certain date.

Think of it like a movie ticket booking:
- You pay a small amount (premium) to reserve a seat
- You have the right to watch the movie, but you don't have to go
- If you don't go, you just lose the booking amount

In the stock market:
- The "something" is usually NIFTY index or stocks
- The "fixed price" is called the **strike price**
- The "certain date" is called the **expiry date**
- The "small amount" is called the **premium**

---

## Call vs Put Options

### Call Option (CE)
- Gives the right to **BUY** at a fixed price
- Buyers profit when market **goes UP**
- Example: NIFTY is at 24,000. You buy a 24,500 CE. If NIFTY goes to 25,000, your option becomes valuable.

### Put Option (PE)
- Gives the right to **SELL** at a fixed price
- Buyers profit when market **goes DOWN**
- Example: NIFTY is at 24,000. You buy a 23,500 PE. If NIFTY falls to 23,000, your option becomes valuable.

---

## Buying vs Selling Options

### Option Buyer
- Pays premium upfront
- Has **limited loss** (only the premium paid)
- Has **unlimited profit** potential
- Needs market to move significantly to profit
- Time works **against** them (options lose value daily)

### Option Seller
- Receives premium upfront
- Has **limited profit** (only the premium received)
- Has **unlimited loss** potential (theoretically)
- Profits when market stays within a range
- Time works **in their favor** (they keep the premium if nothing happens)

**Key Insight:** About 80% of options expire worthless. This means option sellers win most of the time!

---

## What is a Strangle?

A **strangle** is selling (or buying) both a Call and a Put option at the same time, but at different strike prices.

```
Example: NIFTY at 24,000

SELL 24,500 CE (Call) → Receive ₹50 premium
SELL 23,500 PE (Put)  → Receive ₹50 premium
                        ─────────────────────
Total Premium Received: ₹100

Profit Zone: NIFTY stays between 23,500 and 24,500
```

Visual representation:
```
     Loss ←──────────────────────────────────→ Loss
              │                        │
              │      PROFIT ZONE       │
              │                        │
    ──────────┴────────────────────────┴──────────
           23,500      24,000      24,500
           (PE sold)   (Spot)     (CE sold)
```

---

## Why Sell Strangles?

### 1. High Probability of Profit
- Market moves big only 20% of the time
- 80% of the time, it stays in a range
- Strangle sellers profit when market stays in range

### 2. Time Decay (Theta) Works for You
- Every day, options lose some value
- This lost value goes into the seller's pocket
- Even if market doesn't move, you make money

### 3. Premium Collection
- You receive money upfront
- If NIFTY stays between your strikes, you keep ALL of it

### 4. Flexibility
- Can adjust positions if market moves
- Can exit early to book profits
- Can "move" strikes to collect more premium

---

## Understanding Delta

**Delta** measures how much an option's price changes when the underlying (NIFTY) moves by 1 point.

### Delta Values
| Delta | Meaning |
|-------|---------|
| 0.50 (50) | Option price moves ₹0.50 for every ₹1 move in NIFTY |
| 0.30 (30) | Option price moves ₹0.30 for every ₹1 move in NIFTY |
| 0.07 (7) | Option price moves ₹0.07 for every ₹1 move in NIFTY |

### Delta as Probability
Delta roughly indicates the probability of the option expiring "in the money" (profitable for buyer).

| Delta | Probability of Expiring ITM |
|-------|----------------------------|
| 0.50 | 50% chance |
| 0.30 | 30% chance |
| 0.07 | 7% chance |

**Lower delta = Further from current price = Lower probability of being hit**

---

## Why 7-Delta Strikes?

We sell options at **7-delta** strikes because:

### 1. High Win Rate
- 7-delta means only ~7% chance of NIFTY reaching that strike
- **93% probability** of the option expiring worthless (we keep full premium)

### 2. Far Enough from Current Price
- 7-delta strikes are typically 400-600 points away from NIFTY
- Gives buffer room for normal market movements

### 3. Decent Premium
- Still collects reasonable premium (unlike 2-3 delta which pays very little)
- Good balance between risk and reward

### 4. Manageable Risk
- If market moves against us, we have time to adjust
- Not too close to panic immediately

```
Example: NIFTY at 24,000

7-Delta CE might be at: 24,500 (500 points above)
7-Delta PE might be at: 23,500 (500 points below)

Market needs to move 500+ points for us to be in trouble.
That's a 2%+ move - doesn't happen every day!
```

---

## The 14-Day to 7-Day Cycle

We sell options with **~14 days to expiry** and close them at **~7 days to expiry**.

### Why Start at 14 Days?

1. **Good Premium**: 14-day options still have decent time value
2. **Not Too Long**: Don't want to hold positions for months
3. **Weekly Expiries**: NIFTY has weekly expiries, so we can always find ~14 day options

### Why Exit at 7 Days?

1. **Gamma Risk Increases**
   - As expiry approaches, options become more sensitive to price moves
   - A small NIFTY move can cause big option price swings
   - This is dangerous for sellers

2. **Diminishing Returns**
   - Most theta decay happens in last 7 days
   - But risk also increases dramatically
   - Better to exit and re-enter fresh position

3. **Target Achieved**
   - By 7 days, we've usually captured 50-60% of the premium
   - Exit Target % setting controls this (default: 50%)

### The Cycle
```
Week 1: SELL 14-day strangle → Collect premium
Week 2: Position now has 7 days left → EXIT (book profit)
        SELL new 14-day strangle → Collect fresh premium
Week 3: Position now has 7 days left → EXIT (book profit)
        ... and so on
```

---

## What is VWAP?

**VWAP** = Volume Weighted Average Price

It's the average price at which an instrument has traded throughout the day, weighted by volume.

### Simple Explanation
- If lots of trading happened at ₹100, VWAP will be close to ₹100
- If some trading happened at ₹90 and some at ₹110, VWAP will be around ₹100
- VWAP represents the "fair price" for the day

### Why VWAP Matters
- **Price > VWAP**: Current price is "expensive" compared to day's average
- **Price < VWAP**: Current price is "cheap" compared to day's average

VWAP is used by big institutions to gauge if they're getting a good price.

---

## Entry Signal: Straddle Price > VWAP

Our entry condition: **ATM Straddle Price > VWAP of Straddle**

### What is ATM Straddle?
- ATM = At The Money (strike closest to current NIFTY price)
- Straddle = CE + PE at same strike
- ATM Straddle Price = Price of ATM CE + Price of ATM PE

### What Does This Signal Mean?

When **Straddle Price > VWAP**:

1. **Options are "expensive" right now**
   - Current premiums are higher than day's average
   - Good time to SELL (we get more premium)

2. **Volatility expectation is high**
   - Market participants are paying more for options
   - Often happens during uncertainty or after a move
   - But actual move may not happen → we profit

3. **Mean Reversion Expected**
   - Prices tend to come back to average (VWAP)
   - If we sell when expensive, prices likely to fall
   - We can buy back cheaper later

### Why 5-Minute Sustained Signal?
- We wait for condition to be true for 5 continuous minutes
- Avoids false signals from momentary spikes
- Confirms the elevated premium is sustained

---

## Why We Need the Move Functionality

As time passes, our sold options **decay** (lose value). This is good for profits, but creates a problem.

### The Decay Problem

```
Day 1: Sold 24,500 CE at 7-delta (₹50 premium)
Day 5: Same CE is now at 3-delta (₹15 premium)

What happened?
- Time passed → option lost value (good!)
- Option moved further OTM → delta decreased
- We've made ₹35 profit so far
```

### Why is Low Delta Bad?

At 3-delta:
- Option is almost worthless (₹15)
- Very little premium left to decay
- We're "sitting" on a position earning almost nothing
- Capital is blocked for minimal gain

### The Move Solution

**Move** = Close current position + Open new position at 7-delta

```
BEFORE MOVE:
- Position: 24,500 CE at 3-delta (₹15)
- Potential remaining profit: ~₹15

AFTER MOVE:
- Buy back 24,500 CE at ₹15 (book ₹35 profit)
- Sell new 7-delta CE (maybe 24,800 CE at ₹45)
- New potential profit: ~₹45

Total: ₹35 booked + ₹45 potential = ₹80!
```

### Move Decay Threshold

The **Move Decay %** setting (default: 60%) controls when to move:

- Target Delta: 7 (0.07)
- Move Decay: 60%
- Move Trigger: 7 × 60% = 4.2 delta

When option delta falls below 4.2, it's time to move!

### Benefits of Moving

1. **Continuous Premium Collection**
   - Don't let profits stagnate
   - Always have "fresh" premium to collect

2. **Better Capital Utilization**
   - Same capital, more premium
   - Compounding effect over time

3. **Maintain Hedge**
   - 7-delta strikes stay relevant to current NIFTY
   - Decayed strikes are too far to provide meaningful hedge

---

## Putting It All Together

### The Complete Strategy

1. **Wait for Entry Signal**
   - Straddle Price > VWAP for 5 minutes
   - Within trading window (9:30 AM - 3:15 PM)

2. **Enter Position**
   - Sell 7-delta CE (above current NIFTY)
   - Sell 7-delta PE (below current NIFTY)
   - Use ~14 day expiry

3. **Monitor Position**
   - Track unrealized P&L
   - Watch delta decay

4. **Auto-Move (if enabled)**
   - When delta decays below threshold (e.g., 4.2)
   - Automatically move to new 7-delta strike
   - Book profits and collect fresh premium

5. **Auto-Exit (if enabled)**
   - When profit reaches target (e.g., 50% of premium)
   - Exit entire position
   - Usually happens around 7 days to expiry

6. **Repeat**
   - Enter new position with fresh 14-day expiry
   - Continue the cycle

### Risk Management

| Risk | Mitigation |
|------|-----------|
| Big market move | 7-delta gives ~500pt buffer |
| Gap up/down | Position sizing (don't over-leverage) |
| Volatility spike | Move functionality adjusts strikes |
| Time decay stalls | Move to fresh strikes |

### Expected Outcomes

- **Most weeks (80%+)**: Full or partial profit
- **Some weeks (15%)**: Small loss or breakeven
- **Rare weeks (5%)**: Significant loss (big market move)

**Key**: Consistent small profits > Occasional big losses

---

## Glossary

| Term | Meaning |
|------|---------|
| ATM | At The Money - strike closest to current price |
| OTM | Out of The Money - strike away from current price |
| ITM | In The Money - strike that has intrinsic value |
| Premium | Price of the option |
| Strike | The fixed price in the option contract |
| Expiry | Date when option contract ends |
| Delta | Sensitivity of option price to underlying move |
| Theta | Daily time decay of option |
| Gamma | Rate of change of delta |
| VWAP | Volume Weighted Average Price |
| Strangle | Selling CE and PE at different strikes |
| Straddle | Selling CE and PE at same strike |

---

## Quick Reference Card

```
┌─────────────────────────────────────────────────────────┐
│                 STRANGLE SELLING STRATEGY                │
├─────────────────────────────────────────────────────────┤
│  ENTRY                                                   │
│  ├─ Signal: Straddle Price > VWAP (5 min sustained)     │
│  ├─ Delta: 7 (both CE and PE)                           │
│  ├─ Expiry: ~14 days                                    │
│  └─ Time: 9:30 AM - 3:15 PM                             │
├─────────────────────────────────────────────────────────┤
│  EXIT                                                    │
│  ├─ Target: 50% of collected premium                    │
│  ├─ Or: ~7 days to expiry                               │
│  └─ Time: 9:15 AM - 3:30 PM                             │
├─────────────────────────────────────────────────────────┤
│  MOVE                                                    │
│  ├─ Trigger: Delta < (Target × Decay%)                  │
│  ├─ Example: 7 × 60% = 4.2 delta                        │
│  ├─ Action: Close old, open new at 7-delta              │
│  └─ Time: 9:30 AM - 3:15 PM                             │
├─────────────────────────────────────────────────────────┤
│  WIN RATE: ~80% of weeks profitable                      │
│  KEY: Consistency + Risk Management                      │
└─────────────────────────────────────────────────────────┘
```

---

*This manual is for educational purposes. Options trading involves significant risk. Please understand the risks before trading with real money.*
