import { useEffect, useState } from "react";
import api from "../../../axios";

export default function DiscordMessagePreference() {
  const [enabled, setEnabled] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [message, setMessage] = useState("");
  useEffect(() => {
    api.get("/api/admin/discord/user-messaging/preference")
      .then(({ data }) => { setEnabled(!!data.enabled); setLoaded(true); })
      .catch(() => setMessage("Couldn't load messaging preference."));
  }, []);
  const change = async (next) => {
    setMessage("");
    try {
      const { data } = await api.put("/api/admin/discord/user-messaging/preference", { enabled: next });
      setEnabled(!!data.enabled);
      setMessage("Preference saved.");
    } catch {
      setMessage("Couldn't save preference.");
    }
  };
  return <section className="v2-account-section" style={{ padding: 16, marginTop: 16 }}>
    <h2>Discord messages</h2>
    <p>Choose whether EAFC.Live may send you occasional promotional messages by Discord DM. You can turn this off at any time.</p>
    <label style={{ display: "flex", gap: 10, alignItems: "center", marginTop: 12 }}>
      <input type="checkbox" checked={enabled} disabled={!loaded} onChange={e => change(e.target.checked)} />
      Allow promotional Discord messages
    </label>
    {message && <p role="status">{message}</p>}
  </section>;
}
