import { useState } from 'react';
import AddEventForm from './components/AddEventForm';
import EventsList from './components/EventsList';

function App() {
  const [activeTab, setActiveTab] = useState('list');

  return (
    <div style={{ minHeight: '100vh', backgroundColor: '#f5f5f5' }}>
      <nav style={{
        backgroundColor: '#007bff',
        padding: '1rem',
        marginBottom: '2rem'
      }}>
        <h1 style={{ color: 'white', margin: 0, textAlign: 'center' }}>
          Events Manager
        </h1>
      </nav>

      <div style={{ maxWidth: '1000px', margin: '0 auto', padding: '0 20px' }}>
        <div style={{
          display: 'flex',
          gap: '10px',
          marginBottom: '20px',
          justifyContent: 'center'
        }}>
          <button
            onClick={() => setActiveTab('list')}
            style={{
              padding: '10px 20px',
              backgroundColor: activeTab === 'list' ? '#007bff' : '#6c757d',
              color: 'white',
              border: 'none',
              borderRadius: '4px',
              cursor: 'pointer'
            }}
          >
            View Events
          </button>
          <button
            onClick={() => setActiveTab('add')}
            style={{
              padding: '10px 20px',
              backgroundColor: activeTab === 'add' ? '#007bff' : '#6c757d',
              color: 'white',
              border: 'none',
              borderRadius: '4px',
              cursor: 'pointer'
            }}
          >
            Add Event
          </button>
        </div>

        {activeTab === 'list' && <EventsList />}
        {activeTab === 'add' && <AddEventForm />}
      </div>
    </div>
  );
}

export default App;
