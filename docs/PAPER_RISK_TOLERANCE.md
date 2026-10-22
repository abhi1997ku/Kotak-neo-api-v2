# v3.2 planned-risk tolerance

The paper agent uses a ₹2,700 planned-risk target with a ₹270 (10%) upper allowance. This is an upper tolerance, not a minimum risk requirement. Position size stays three lots and the initial premium stop stays 30 points.

Entry allowance = min(₹2,970, ₹5,400 + min(0, realized P&L) + ₹270).

The daily allowance is shared: it is not multiplied by the number of trades. Profits do not expand the loss budget. The existing two-loss lockout and realized loss lockout at ₹5,400 still prohibit new entries. The maximum projected daily loss accepted at entry is ₹5,670; actual stop fills can exceed it. This is not a guaranteed maximum loss.

For October 14's first loss of ₹2,857.50, a second trade with ₹2,700 planned risk projects ₹5,557.50 daily loss, so it passes this check. Other entry checks still apply. A first loss above ₹2,970 would block another ₹2,700 planned-risk trade. This change does not establish that previously skipped trades would have been profitable.

Trade-open events record planned risk, permitted risk, tolerance and tolerance used. Status exposes the policy. No live broker orders are enabled by this change.
