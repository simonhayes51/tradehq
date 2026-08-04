import React, { useState } from "react";
import { createEvent } from "../api/eventsApi.js";

export default function SimpleAddEvent() {
  const [show, setShow] = useState(false);
  const [form, setForm] = useState({ name: '', kind: '', start_at: '' });
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (e) => {
    e.preventDefault();
    setLoading(true);
    try {
      await createEvent({ 
        ...form, 
        confidence: 'confirmed', 
        source: 'admin' 
      });
      setForm({ name: '', kind: '', start_at: '' });
      setShow(false);
      alert('Event added!');
    } catch (err) {
      console.error('Event creation error:', err);
      alert(`Failed to add event: ${err.message}`);
    }
    setLoading(false);
  };

  if (!show) {
    return (
      <button 
        onClick={() => setShow(true)}
        className="px-4 py-2 bg-blue-600 text-white rounded hover:bg-blue-700"
      >
        + Add Event
      </button>
    );
  }

  return (
    <div className="p-4 bg-gray-800 rounded border border-gray-700">
      <form onSubmit={handleSubmit} className="space-y-3">
        <input
          type="text"
          placeholder="Event name"
          value={form.name}
          onChange={(e) => setForm({...form, name: e.target.value})}
          required
          className="w-full px-3 py-2 bg-gray-700 text-white rounded"
        />
        <div className="flex gap-2">
          <input
            type="text"
            placeholder="Type (e.g., promo)"
            value={form.kind}
            onChange={(e) => setForm({...form, kind: e.target.value})}
            required
            className="flex-1 px-3 py-2 bg-gray-700 text-white rounded"
          />
          <input
            type="datetime-local"
            value={form.start_at}
            onChange={(e) => setForm({...form, start_at: e.target.value})}
            required
            className="flex-1 px-3 py-2 bg-gray-700 text-white rounded"
          />
        </div>
        <div className="flex gap-2">
          <button 
            type="submit" 
            disabled={loading}
            className="px-3 py-2 bg-green-600 text-white rounded hover:bg-green-700 disabled:opacity-50"
          >
            {loading ? 'Adding...' : 'Add Event'}
          </button>
          <button 
            type="button" 
            onClick={() => setShow(false)}
            className="px-3 py-2 bg-gray-600 text-white rounded hover:bg-gray-500"
          >
            Cancel
          </button>
        </div>
      </form>
    </div>
  );
}
