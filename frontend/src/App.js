import { useState, useEffect } from "react";
import "./App.css";
import axios from "axios";

const BACKEND_URL = process.env.REACT_APP_BACKEND_URL;
const API = `${BACKEND_URL}/api`;

// Admin Upload Component
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
      <div className="bg-white p-6 rounded-lg shadow-lg max-w-md mx-auto">
        <h2 className="text-2xl font-bold mb-4 text-center">Admin Login</h2>
        <form onSubmit={handleLogin} className="space-y-4">
          <input
            type="password"
            placeholder="Passwort eingeben..."
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            className="w-full p-3 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500"
            required
          />
          <button
            type="submit"
            className="w-full bg-blue-500 text-white p-3 rounded-lg hover:bg-blue-600 transition-colors"
          >
            Einloggen
          </button>
        </form>
      </div>
    );
  }

  return (
    <div className="bg-white p-6 rounded-lg shadow-lg">
      <h2 className="text-2xl font-bold mb-4 text-center">📁 Dateien Hochladen</h2>
      
      <div
        className={`border-2 border-dashed p-8 rounded-lg text-center transition-colors ${
          dragActive 
            ? 'border-blue-500 bg-blue-50' 
            : 'border-gray-300 hover:border-gray-400'
        }`}
        onDragEnter={handleDrag}
        onDragLeave={handleDrag}
        onDragOver={handleDrag}
        onDrop={handleDrop}
      >
        <div className="space-y-4">
          <div className="text-4xl">📎</div>
          <p className="text-lg text-gray-600">
            Dateien hier hinziehen oder auswählen
          </p>
          <p className="text-sm text-gray-500">
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
            className="inline-block bg-blue-500 text-white px-6 py-3 rounded-lg cursor-pointer hover:bg-blue-600 transition-colors"
          >
            Dateien Auswählen
          </label>
        </div>
      </div>

      {uploading && (
        <div className="mt-4 text-center">
          <div className="inline-flex items-center px-4 py-2 bg-blue-100 text-blue-800 rounded-lg">
            <div className="animate-spin rounded-full h-4 w-4 border-b-2 border-blue-800 mr-2"></div>
            Wird hochgeladen...
          </div>
        </div>
      )}

      <button
        onClick={() => {
          localStorage.removeItem('adminToken');
          onLogin(false);
        }}
        className="mt-4 text-red-500 hover:text-red-700 text-sm"
      >
        Abmelden
      </button>
    </div>
  );
};

// Media Gallery Component
const MediaGallery = ({ files, onDeleteFile, isAdmin }) => {
  const getFileIcon = (mimeType) => {
    if (mimeType.startsWith('image/')) return '🖼️';
    if (mimeType.startsWith('video/')) return '🎥';
    if (mimeType.includes('pdf')) return '📄';
    if (mimeType.includes('document') || mimeType.includes('word')) return '📝';
    if (mimeType.includes('zip') || mimeType.includes('rar')) return '📁';
    return '📎';
  };

  const formatFileSize = (bytes) => {
    if (bytes === 0) return '0 Bytes';
    const k = 1024;
    const sizes = ['Bytes', 'KB', 'MB', 'GB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
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
        <h3 className="text-xl text-gray-600">Noch keine Dateien hochgeladen</h3>
        <p className="text-gray-500">Als Admin können Sie oben Dateien hinzufügen</p>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <h2 className="text-2xl font-bold text-center mb-6">📚 MagoApp Galerie</h2>
      
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
        {files.map((file) => (
          <div key={file.id} className="bg-white rounded-lg shadow-lg overflow-hidden">
            <div className="p-4">
              <div className="flex items-center justify-between mb-3">
                <span className="text-2xl">{getFileIcon(file.mime_type)}</span>
                {isAdmin && (
                  <button
                    onClick={() => handleDelete(file.id)}
                    className="text-red-500 hover:text-red-700 text-sm"
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
              
              <h3 className="font-semibold text-sm truncate mb-2" title={file.original_filename}>
                {file.original_filename}
              </h3>
              
              <div className="text-xs text-gray-500 space-y-1">
                <p>Größe: {formatFileSize(file.file_size)}</p>
                <p>Datum: {new Date(file.upload_date).toLocaleDateString('de-DE')}</p>
              </div>
              
              <a
                href={`${API}/media/download/${file.id}`}
                download={file.original_filename}
                className="block w-full bg-green-500 text-white text-center py-2 rounded mt-3 hover:bg-green-600 transition-colors text-sm"
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
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    // Check if already logged in
    const token = localStorage.getItem('adminToken');
    if (token) {
      setIsAdmin(true);
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

  if (loading) {
    return (
      <div className="min-h-screen bg-gray-100 flex items-center justify-center">
        <div className="text-center">
          <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-blue-500 mx-auto mb-4"></div>
          <p>Lade Medien...</p>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-gray-100">
      <div className="container mx-auto px-4 py-8 max-w-6xl">
        <div className="text-center mb-8">
          <h1 className="text-4xl font-bold text-gray-800 mb-2">
            📱 MagoApp
          </h1>
          <p className="text-gray-600">
            WhatsApp-ähnliche Medien-Sharing App
          </p>
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
        </div>
      </div>
    </div>
  );
}

export default App;