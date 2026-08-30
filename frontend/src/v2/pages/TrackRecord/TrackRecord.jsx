// src/v2/pages/TrackRecord/TrackRecord.jsx
//
// The full performance record: window tabs, the chronological ledger, the
// recent wins/losses split, the model-validation disclosure and the
// methodology note. All of this used to sit on the homepage, where it was
// by far the largest section on the page. It is a reference page, so it
// lives on a page.
import { Link } from "react-router-dom";
import { ArrowLeft } from "lucide-react";
import TrackRecordPanel from "../../components/TrackRecordPanel";
import { PageHead } from "../Players/Players";
import "../../styles/v2-destinations.css";

export default function TrackRecord() {
  return (
    <main className="v2-destination">
      <PageHead
        eyebrow="TRACK RECORD"
        title="Every published call, graded"
        copy="Recommendations we actually sent to users, graded against the real chronological price history. Results are hypothetical - they show what the market did, not what any individual made."
        action={<Link className="v2-primary-link" to="/"><ArrowLeft size={15} /> Back to Home</Link>}
      />
      <TrackRecordPanel />
    </main>
  );
}
