import { Crown, Clock, ArrowRight } from "lucide-react";
import { Link } from "react-router-dom";
import SectionCard from "../../components/SectionCard";
import { useEntitlements } from "../../../context/EntitlementsContext";
import "../../styles/billing.css";

export default function PreseasonBilling() {
  const { promo } = useEntitlements();
  const until = promo?.until ? new Date(promo.until) : null;
  const untilLabel = until && !Number.isNaN(until.getTime())
    ? until.toLocaleDateString("en-GB", { day: "numeric", month: "long" })
    : "FC27 early access";

  return (
    <main className="v2-destination billing-v2">
      <div className="billing-v2-inner">
        <header className="billing-v2-head">
          <span className="billing-v2-badge"><Crown size={14} /> ELITE UNLOCKED</span>
          <h1>Elite is free until FC27</h1>
          <p>You have the full personal Elite experience right now. No card, Stripe trial or subscription is needed.</p>
        </header>

        <SectionCard title="Preseason access" className="billing-v2-plan">
          <div className="billing-v2-plan-row">
            <div>
              <strong>£0 during preseason</strong>
              <p><Clock size={13} style={{ verticalAlign: "-2px", marginRight: 6 }} />Free access ends {untilLabel}. After that, your account returns to Free unless you have a paid or manually granted Elite entitlement.</p>
            </div>
          </div>
          <div className="billing-v2-actions">
            <Link to="/opportunities" className="billing-v2-btn primary">
              Open Opportunities <ArrowRight size={14} />
            </Link>
            <Link to="/discord-feed" className="billing-v2-btn secondary">
              Manage a Server Feed
            </Link>
          </div>
        </SectionCard>

        <SectionCard title="What happens at FC27?">
          <p style={{ margin: 0 }}>
            This giveaway is separate from the normal Elite subscription and its standard Stripe trial. We will not create a subscription or charge a payment method when the preseason access ends.
          </p>
        </SectionCard>
      </div>
    </main>
  );
}
