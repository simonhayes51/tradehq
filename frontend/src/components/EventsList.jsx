import { useState, useEffect } from 'react';
import { fetchAllEvents, deleteEvent } from '../api/eventsApi.js';

function EventsList() {
  const [events, setEvents] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  useEffect(() => {
    loadEvents();
  }, []);

  const loadEvents = async () => {
    try {
      setLoading(true);
      const eventsData = await fetchAllEvents();
      setEvents(eventsData);
      setError('');
    } catch (err) {
      setError(`Failed to load events: ${err.message}`);
    } finally {
      setLoading(false);
    }
  };

  const handleDelete = async (eventId) => {
    if (!confirm('Are you sure you want to delete this event?')) {
      return;
    }

    try {
      await deleteEvent(eventId);
      setEvents(events.filter(event => event.id !== eventId));
    } catch (err) {
      setError(`Failed to delete event: ${err.message}`);
    }
  };

  const formatDateTime = (dateString) => {
    if (!dateString) return 'Not set';
    return new Date(dateString).toLocaleString();
  };

  if (loading) {
    return <div style={{ padding: '20px', textAlign: 'center' }}>Loading events...</div>;
  }

  return (
    <div style={{ maxWidth: '800px', margin: '20px auto', padding: '20px' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '20px' }}>
        <h2>Events</h2>
        <button
          onClick={loadEvents}
          style={{
            padding: '8px 16px',
            backgroundColor: '#28a745',
            color: 'white',
            border: 'none',
            borderRadius: '4px',
            cursor: 'pointer'
          }}
        >
          Refresh
        </button>
      </div>

      {error && (
        <div style={{
          padding: '10px',
          backgroundColor: '#ffebee',
          color: '#c62828',
          borderRadius: '4px',
          marginBottom: '20px'
        }}>
          {error}
        </div>
      )}

      {events.length === 0 ? (
        <div style={{ textAlign: 'center', padding: '40px', color: '#666' }}>
          No events found
        </div>
      ) : (
        <div style={{ display: 'grid', gap: '16px' }}>
          {events.map((event) => (
            <div
              key={event.id}
              style={{
                border: '1px solid #ddd',
                borderRadius: '8px',
                padding: '16px',
                backgroundColor: '#f9f9f9'
              }}
            >
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
                <div style={{ flex: 1 }}>
                  <h3 style={{ margin: '0 0 8px 0', color: '#333' }}>
                    {event.name}
                  </h3>
                  
                  <div style={{ marginBottom: '4px' }}>
                    <strong>Kind:</strong> <span style={{ 
                      backgroundColor: '#e3f2fd', 
                      padding: '2px 8px', 
                      borderRadius: '12px',
                      fontSize: '12px'
                    }}>
                      {event.kind}
                    </span>
                  </div>
                  
                  <div style={{ marginBottom: '4px' }}>
                    <strong>Start:</strong> {formatDateTime(event.start_at)}
                  </div>
                  
                  {event.end_at && (
                    <div style={{ marginBottom: '4px' }}>
                      <strong>End:</strong> {formatDateTime(event.end_at)}
                    </div>
                  )}
                  
                  <div style={{ marginBottom: '4px' }}>
                    <strong>Confidence:</strong> {event.confidence}
                  </div>
                  
                  <div style={{ marginBottom: '4px' }}>
                    <strong>Source:</strong> {event.source}
                  </div>
                  
                  <div style={{ fontSize: '12px', color: '#666' }}>
                    Created: {formatDateTime(event.created_at)}
                  </div>
                </div>
                
                <button
                  onClick={() => handleDelete(event.id)}
                  style={{
                    padding: '4px 8px',
                    backgroundColor: '#dc3545',
                    color: 'white',
                    border: 'none',
                    borderRadius: '4px',
                    cursor: 'pointer',
                    fontSize: '12px',
                    marginLeft: '16px'
                  }}
                >
                  Delete
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export default EventsList;
