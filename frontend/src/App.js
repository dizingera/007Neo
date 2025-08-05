import { useState, useEffect } from "react";
import "./App.css";
import axios from "axios";

const BACKEND_URL = process.env.REACT_APP_BACKEND_URL;
const API = `${BACKEND_URL}/api`;

// User Registration Component
const UserRegistration = ({ onUserLoggedIn }) => {
  const [isLogin, setIsLogin] = useState(false);
  const [formData, setFormData] = useState({
    phoneNumber: "",
    firstName: "",
    lastName: ""
  });
  const [loading, setLoading] = useState(false);

  const handleRegister = async (e) => {
    e.preventDefault();
    setLoading(true);
    
    try {
      const response = await axios.post(`${API}/users/register`, {
        phone_number: formData.phoneNumber,
        first_name: formData.firstName,
        last_name: formData.lastName
      });
      
      alert('Registrierung erfolgreich! Sie können sich jetzt anmelden.');
      setIsLogin(true);
      setFormData({ phoneNumber: "", firstName: "", lastName: "" });
    } catch (error) {
      const message = error.response?.data?.detail || 'Registrierung fehlgeschlagen';
      alert(message);
      console.error('Registration failed:', error);
    } finally {
      setLoading(false);
    }
  };

  const handleLogin = async (e) => {
    e.preventDefault();
    setLoading(true);
    
    try {
      const response = await axios.post(`${API}/users/login`, {
        phone_number: formData.phoneNumber
      });
      
      localStorage.setItem('userToken', response.data.access_token);
      localStorage.setItem('userData', JSON.stringify(response.data.user));
      onUserLoggedIn(response.data.user);
    } catch (error) {
      const message = error.response?.data?.detail || 'Anmeldung fehlgeschlagen';
      alert(message);
      console.error('Login failed:', error);
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="bg-gray-800 p-6 rounded-lg shadow-lg max-w-md mx-auto">
      <h2 className="text-2xl font-bold mb-4 text-center text-white">
        {isLogin ? "📱 Anmelden" : "📝 Registrierung"}
      </h2>
      
      <form onSubmit={isLogin ? handleLogin : handleRegister} className="space-y-4">
        <div>
          <input
            type="tel"
            placeholder="Handynummer (z.B. +49 123 456789)"
            value={formData.phoneNumber}
            onChange={(e) => setFormData({...formData, phoneNumber: e.target.value})}
            className="w-full p-3 bg-gray-700 text-white border border-gray-600 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500"
            required
          />
        </div>
        
        {!isLogin && (
          <>
            <div>
              <input
                type="text"
                placeholder="Vorname"
                value={formData.firstName}
                onChange={(e) => setFormData({...formData, firstName: e.target.value})}
                className="w-full p-3 bg-gray-700 text-white border border-gray-600 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500"
                required
                minLength="2"
              />
            </div>
            <div>
              <input
                type="text"
                placeholder="Nachname"
                value={formData.lastName}
                onChange={(e) => setFormData({...formData, lastName: e.target.value})}
                className="w-full p-3 bg-gray-700 text-white border border-gray-600 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500"
                required
                minLength="2"
              />
            </div>
          </>
        )}
        
        <button
          type="submit"
          disabled={loading}
          className="w-full bg-blue-600 text-white p-3 rounded-lg hover:bg-blue-700 transition-colors disabled:opacity-50"
        >
          {loading ? "⏳ Lädt..." : (isLogin ? "Anmelden" : "Registrieren")}
        </button>
      </form>
      
      <div className="mt-4 text-center">
        <button
          onClick={() => setIsLogin(!isLogin)}
          className="text-blue-400 hover:text-blue-300 text-sm"
        >
          {isLogin 
            ? "Noch kein Konto? Registrieren" 
            : "Bereits registriert? Anmelden"
          }
        </button>
      </div>
    </div>
  );
};

// User Management Component (Admin only)
const UserManagement = ({ isAdmin }) => {
  const [users, setUsers] = useState([]);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (isAdmin) {
      loadUsers();
    }
  }, [isAdmin]);

  const loadUsers = async () => {
    if (!isAdmin) return;
    
    setLoading(true);
    try {
      const token = localStorage.getItem('adminToken');
      const response = await axios.get(`${API}/admin/users`, {
        headers: { 'Authorization': `Bearer ${token}` }
      });
      setUsers(response.data);
    } catch (error) {
      console.error('Error loading users:', error);
    } finally {
      setLoading(false);
    }
  };

  const deactivateUser = async (userId) => {
    if (!window.confirm('Benutzer wirklich deaktivieren?')) return;
    
    try {
      const token = localStorage.getItem('adminToken');
      await axios.delete(`${API}/admin/users/${userId}`, {
        headers: { 'Authorization': `Bearer ${token}` }
      });
      loadUsers(); // Reload users
      alert('Benutzer wurde deaktiviert');
    } catch (error) {
      console.error('Error deactivating user:', error);
      alert('Fehler beim Deaktivieren des Benutzers');
    }
  };

  if (!isAdmin) return null;

  return (
    <div className="bg-gray-800 p-6 rounded-lg shadow-lg">
      <div className="flex justify-between items-center mb-4">
        <h3 className="text-xl font-bold text-white">👥 Benutzerverwaltung</h3>
        <button
          onClick={loadUsers}
          className="text-blue-400 hover:text-blue-300 text-sm"
        >
          🔄 Aktualisieren
        </button>
      </div>

      {loading ? (
        <div className="text-center py-4">
          <div className="animate-spin rounded-full h-6 w-6 border-b-2 border-blue-500 mx-auto mb-2"></div>
          <p className="text-gray-400">Lade Benutzer...</p>
        </div>
      ) : (
        <div className="space-y-3 max-h-64 overflow-y-auto">
          {users.length === 0 ? (
            <p className="text-gray-400 text-center">Noch keine registrierten Benutzer</p>
          ) : (
            users.map((user) => (
              <div key={user.id} className="bg-gray-700 p-3 rounded flex justify-between items-center">
                <div>
                  <p className="text-white font-semibold">
                    {user.first_name} {user.last_name}
                  </p>
                  <p className="text-gray-400 text-sm">{user.phone_number}</p>
                  <p className="text-gray-500 text-xs">
                    {new Date(user.registration_date).toLocaleDateString('de-DE')}
                  </p>
                </div>
                <div className="flex items-center space-x-2">
                  <span className={`px-2 py-1 text-xs rounded ${
                    user.is_active ? 'bg-green-600 text-white' : 'bg-red-600 text-white'
                  }`}>
                    {user.is_active ? 'Aktiv' : 'Deaktiviert'}
                  </span>
                  {user.is_active && (
                    <button
                      onClick={() => deactivateUser(user.id)}
                      className="text-red-400 hover:text-red-300 text-sm"
                    >
                      🚫
                    </button>
                  )}
                </div>
              </div>
            ))
          )}
        </div>
      )}
    </div>
  );
};

// Chat Component (Updated for authenticated users)
const ChatSection = ({ isAdmin, currentUser }) => {
  const [messages, setMessages] = useState([]);
  const [newMessage, setNewMessage] = useState("");
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    loadMessages();
    // Poll for new messages every 3 seconds
    const interval = setInterval(loadMessages, 3000);
    return () => clearInterval(interval);
  }, []);

  const loadMessages = async () => {
    try {
      const response = await axios.get(`${API}/chat/messages`);
      setMessages(response.data);
    } catch (error) {
      console.error('Error loading messages:', error);
    }
  };

  const sendMessage = async (e) => {
    e.preventDefault();
    if (!newMessage.trim()) return;

    setLoading(true);
    try {
      const token = localStorage.getItem(isAdmin ? 'adminToken' : 'userToken');
      const endpoint = isAdmin ? '/admin/chat/message' : '/chat/message';

      await axios.post(`${API}${endpoint}`, {
        message: newMessage
      }, {
        headers: { 'Authorization': `Bearer ${token}` }
      });
      
      setNewMessage("");
      loadMessages();
    } catch (error) {
      console.error('Error sending message:', error);
      alert('Fehler beim Senden der Nachricht');
    } finally {
      setLoading(false);
    }
  };

  const clearChat = async () => {
    if (!window.confirm('Alle Nachrichten löschen?')) return;
    
    try {
      const token = localStorage.getItem('adminToken');
      await axios.delete(`${API}/admin/chat/clear`, {
        headers: { 'Authorization': `Bearer ${token}` }
      });
      setMessages([]);
    } catch (error) {
      console.error('Error clearing chat:', error);
    }
  };

  return (
    <div className="bg-gray-800 rounded-lg p-4 shadow-lg">
      <div className="flex justify-between items-center mb-4">
        <h3 className="text-xl font-bold text-white">💬 Chat</h3>
        <div className="flex items-center space-x-3">
          {currentUser && !isAdmin && (
            <span className="text-gray-400 text-sm">
              Als: {currentUser.first_name} {currentUser.last_name}
            </span>
          )}
          {isAdmin && (
            <button
              onClick={clearChat}
              className="text-red-400 hover:text-red-300 text-sm"
            >
              🗑️ Löschen
            </button>
          )}
        </div>
      </div>

      <div className="bg-gray-900 rounded-lg p-3 h-64 overflow-y-auto mb-4 space-y-2">
        {messages.length === 0 ? (
          <p className="text-gray-400 text-center">Noch keine Nachrichten...</p>
        ) : (
          messages.map((msg) => (
            <div
              key={msg.id}
              className={`flex mb-3 ${msg.sender === 'admin' ? 'justify-end' : 'justify-start'}`}
            >
              <div
                className={`max-w-xs px-4 py-2 rounded-2xl shadow-sm ${
                  msg.sender === 'admin'
                    ? 'bg-green-600 text-white rounded-br-sm'
                    : 'bg-gray-700 text-gray-100 rounded-bl-sm'
                }`}
              >
                <p className="text-xs opacity-75 mb-1">
                  {msg.sender_name} • {new Date(msg.timestamp).toLocaleTimeString('de-DE', { 
                    hour: '2-digit', 
                    minute: '2-digit' 
                  })}
                </p>
                <p className="text-sm leading-relaxed">{msg.message}</p>
              </div>
            </div>
          ))
        )}
      </div>

      <form onSubmit={sendMessage} className="space-y-3">
        <div className="flex items-end space-x-3">
          <div className="flex-1 bg-gray-700 rounded-3xl px-4 py-3 border border-gray-600 min-h-[48px]">
            <input
              type="text"
              placeholder="Nachricht..."
              value={newMessage}
              onChange={(e) => setNewMessage(e.target.value)}
              className="w-full bg-transparent text-white placeholder-gray-400 focus:outline-none resize-none"
              disabled={loading}
              onKeyPress={(e) => {
                if (e.key === 'Enter' && !e.shiftKey) {
                  e.preventDefault();
                  if (!loading && newMessage.trim()) {
                    sendMessage(e);
                  }
                }
              }}
            />
          </div>
          <button
            type="submit"
            disabled={loading || !newMessage.trim()}
            className={`w-12 h-12 rounded-full flex items-center justify-center transition-all duration-200 ${
              newMessage.trim() && !loading
                ? 'bg-green-600 hover:bg-green-700 scale-100'
                : 'bg-gray-600 scale-90 opacity-50'
            }`}
          >
            {loading ? (
              <div className="animate-spin rounded-full h-5 w-5 border-b-2 border-white"></div>
            ) : (
              <span className="text-xl">➤</span>
            )}
          </button>
        </div>
      </form>
    </div>
  );
};

// Admin Upload Component (unchanged)
const AdminUpload = ({ onFileUploaded, isAdmin, onLogin }) => {
  const [password, setPassword] = useState("");
  const [uploading, setUploading] = useState(false);
  const [dragActive, setDragActive] = useState(false);

  const handleLogin = async (e) => {
    e.preventDefault();
    try {
      const response = await axios.post(`${API}/admin/login`, {
        password: password
      });
      localStorage.setItem('adminToken', response.data.access_token);
      onLogin(true);
      setPassword("");
    } catch (error) {
      alert('Falsches Passwort!');
      console.error('Login failed:', error);
    }
  };

  const handleFileUpload = async (files) => {
    if (!files || files.length === 0) return;

    const token = localStorage.getItem('adminToken');
    if (!token) {
      alert('Bitte zuerst einloggen!');
      return;
    }

    for (let file of files) {
      setUploading(true);
      const formData = new FormData();
      formData.append('file', file);

      try {
        const response = await axios.post(`${API}/admin/upload`, formData, {
          headers: {
            'Content-Type': 'multipart/form-data',
            'Authorization': `Bearer ${token}`
          }
        });
        onFileUploaded(response.data);
      } catch (error) {
        console.error('Upload failed:', error);
        alert(`Fehler beim Hochladen von ${file.name}`);
      } finally {
        setUploading(false);
      }
    }
  };

  const handleDrag = (e) => {
    e.preventDefault();
    e.stopPropagation();
    if (e.type === "dragenter" || e.type === "dragover") {
      setDragActive(true);
    } else if (e.type === "dragleave") {
      setDragActive(false);
    }
  };

  const handleDrop = (e) => {
    e.preventDefault();
    e.stopPropagation();
    setDragActive(false);
    
    if (e.dataTransfer.files && e.dataTransfer.files[0]) {
      handleFileUpload(Array.from(e.dataTransfer.files));
    }
  };

  if (!isAdmin) {
    return (
      <div className="bg-gray-800 p-6 rounded-lg shadow-lg max-w-md mx-auto">
        <h2 className="text-2xl font-bold mb-4 text-center text-white">Admin Login</h2>
        <form onSubmit={handleLogin} className="space-y-4">
          <input
            type="password"
            placeholder="Passwort eingeben..."
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            className="w-full p-3 bg-gray-700 text-white border border-gray-600 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500"
            required
          />
          <button
            type="submit"
            className="w-full bg-blue-600 text-white p-3 rounded-lg hover:bg-blue-700 transition-colors"
          >
            Einloggen
          </button>
        </form>
      </div>
    );
  }

  return (
    <div className="bg-gray-800 p-6 rounded-lg shadow-lg">
      <h2 className="text-2xl font-bold mb-4 text-center text-white">📁 Dateien Hochladen</h2>
      
      <div
        className={`border-2 border-dashed p-8 rounded-lg text-center transition-colors ${
          dragActive 
            ? 'border-blue-500 bg-blue-900 bg-opacity-20' 
            : 'border-gray-600 hover:border-gray-500'
        }`}
        onDragEnter={handleDrag}
        onDragLeave={handleDrag}
        onDragOver={handleDrag}
        onDrop={handleDrop}
      >
        <div className="space-y-4">
          <div className="text-4xl">📎</div>
          <p className="text-lg text-gray-300">
            Dateien hier hinziehen oder auswählen
          </p>
          <p className="text-sm text-gray-400">
            Alle Formate unterstützt: Bilder, Videos, PDFs, Dokumente
          </p>
          
          <input
            type="file"
            multiple
            onChange={(e) => handleFileUpload(Array.from(e.target.files))}
            className="hidden"
            id="fileInput"
            accept="*/*"
          />
          <label
            htmlFor="fileInput"
            className="inline-block bg-blue-600 text-white px-6 py-3 rounded-lg cursor-pointer hover:bg-blue-700 transition-colors"
          >
            Dateien Auswählen
          </label>
        </div>
      </div>

      {uploading && (
        <div className="mt-4 text-center">
          <div className="inline-flex items-center px-4 py-2 bg-blue-900 bg-opacity-50 text-blue-300 rounded-lg">
            <div className="animate-spin rounded-full h-4 w-4 border-b-2 border-blue-300 mr-2"></div>
            Wird hochgeladen...
          </div>
        </div>
      )}

      <button
        onClick={() => {
          localStorage.removeItem('adminToken');
          onLogin(false);
        }}
        className="mt-4 text-red-400 hover:text-red-300 text-sm"
      >
        Abmelden
      </button>
    </div>
  );
};

// Media Gallery Component (unchanged)
const MediaGallery = ({ files, onDeleteFile, isAdmin }) => {
  const getFileIcon = (mimeType) => {
    if (mimeType.startsWith('image/')) return '🖼️';
    if (mimeType.startsWith('video/')) return '🎥';
    if (mimeType.includes('pdf')) return '📄';
    if (mimeType.includes('document') || mimeType.includes('word')) return '📝';
    if (mimeType.includes('zip') || mimeType.includes('rar')) return '📁';
    return '📎';
  };

  const handleDelete = async (fileId) => {
    if (!window.confirm('Datei wirklich löschen?')) return;
    
    try {
      const token = localStorage.getItem('adminToken');
      await axios.delete(`${API}/admin/media/${fileId}`, {
        headers: { 'Authorization': `Bearer ${token}` }
      });
      onDeleteFile(fileId);
    } catch (error) {
      console.error('Delete failed:', error);
      alert('Fehler beim Löschen der Datei');
    }
  };

  if (files.length === 0) {
    return (
      <div className="text-center py-12">
        <div className="text-6xl mb-4">📁</div>
        <h3 className="text-xl text-gray-300">Noch keine Dateien hochgeladen</h3>
        <p className="text-gray-400">Als Admin können Sie oben Dateien hinzufügen</p>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <h2 className="text-2xl font-bold text-center mb-6 text-white">📚 MagoApp Galerie</h2>
      
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
        {files.map((file) => (
          <div key={file.id} className="bg-gray-800 rounded-lg shadow-lg overflow-hidden">
            <div className="p-4">
              <div className="flex items-center justify-between mb-3">
                <span className="text-2xl">{getFileIcon(file.mime_type)}</span>
                {isAdmin && (
                  <button
                    onClick={() => handleDelete(file.id)}
                    className="text-red-400 hover:text-red-300 text-sm"
                  >
                    🗑️
                  </button>
                )}
              </div>
              
              {file.mime_type.startsWith('image/') && (
                <div className="mb-3">
                  <img
                    src={`${API}/media/preview/${file.id}`}
                    alt={file.original_filename}
                    className="w-full h-32 object-cover rounded"
                    loading="lazy"
                  />
                </div>
              )}
              
              <a
                href={`${API}/media/download/${file.id}`}
                download={file.original_filename}
                className="block w-full bg-green-600 text-white text-center py-2 rounded hover:bg-green-700 transition-colors text-sm"
              >
                ⬇️ Herunterladen
              </a>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
};

// Main App Component
function App() {
  const [files, setFiles] = useState([]);
  const [isAdmin, setIsAdmin] = useState(false);
  const [currentUser, setCurrentUser] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    // Check if admin is logged in
    const adminToken = localStorage.getItem('adminToken');
    if (adminToken) {
      setIsAdmin(true);
    }
    
    // Check if user is logged in
    const userToken = localStorage.getItem('userToken');
    const userData = localStorage.getItem('userData');
    if (userToken && userData) {
      setCurrentUser(JSON.parse(userData));
    }
    
    loadFiles();
  }, []);

  const loadFiles = async () => {
    try {
      const response = await axios.get(`${API}/media/files`);
      setFiles(response.data);
    } catch (error) {
      console.error('Error loading files:', error);
    } finally {
      setLoading(false);
    }
  };

  const handleFileUploaded = (newFile) => {
    setFiles(prev => [newFile, ...prev]);
  };

  const handleFileDeleted = (fileId) => {
    setFiles(prev => prev.filter(f => f.id !== fileId));
  };

  const handleUserLoggedIn = (user) => {
    setCurrentUser(user);
  };

  const handleLogout = () => {
    localStorage.removeItem('userToken');
    localStorage.removeItem('userData');
    setCurrentUser(null);
  };

  if (loading) {
    return (
      <div className="min-h-screen bg-black flex items-center justify-center">
        <div className="text-center">
          <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-blue-500 mx-auto mb-4"></div>
          <p className="text-white">Lade Medien...</p>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-black text-white">
      <div className="container mx-auto px-4 py-8 max-w-6xl">
        <div className="text-center mb-8">
          <h1 className="text-4xl font-bold text-white mb-2">
            📱 MagoApp
          </h1>
          <p className="text-gray-400">
            WhatsApp-ähnliche Medien-Sharing App
          </p>
          
          {currentUser && (
            <div className="mt-2 flex justify-center items-center space-x-4">
              <span className="text-green-400 text-sm">
                👋 Willkommen, {currentUser.first_name}!
              </span>
              <button
                onClick={handleLogout}
                className="text-red-400 hover:text-red-300 text-sm"
              >
                Abmelden
              </button>
            </div>
          )}
        </div>

        <div className="space-y-8">
          <AdminUpload 
            onFileUploaded={handleFileUploaded}
            isAdmin={isAdmin}
            onLogin={setIsAdmin}
          />
          
          <MediaGallery 
            files={files}
            onDeleteFile={handleFileDeleted}
            isAdmin={isAdmin}
          />
          
          <UserManagement isAdmin={isAdmin} />
          
          {/* Show user registration if not logged in and not admin */}
          {!currentUser && !isAdmin ? (
            <UserRegistration onUserLoggedIn={handleUserLoggedIn} />
          ) : (
            <ChatSection isAdmin={isAdmin} currentUser={currentUser} />
          )}
        </div>
      </div>
    </div>
  );
}

export default App;