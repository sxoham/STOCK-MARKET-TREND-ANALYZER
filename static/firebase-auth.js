import { initializeApp } from "https://www.gstatic.com/firebasejs/10.8.0/firebase-app.js";
import {
  getAuth,
  signInWithEmailAndPassword,
  createUserWithEmailAndPassword,
  onAuthStateChanged,
  signOut,
  GoogleAuthProvider,
  signInWithPopup,
  deleteUser,
  sendEmailVerification
} from "https://www.gstatic.com/firebasejs/10.8.0/firebase-auth.js";

const firebaseConfig = {
  apiKey: "AIzaSyDOX9cd-APwu_osc3LC6Mwe6aXCPMj45gw",
  authDomain: "trendanalyzer-4857f.firebaseapp.com",
  projectId: "trendanalyzer-4857f",
  storageBucket: "trendanalyzer-4857f.firebasestorage.app",
  messagingSenderId: "962982922636",
  appId: "1:962982922636:web:1c56b7601fa4d643d06ea9",
  measurementId: "G-B45MTT2HX5"
};

const app = initializeApp(firebaseConfig);
console.log("Firebase App Initialized:", app.name);

const auth = getAuth(app);
const googleProvider = new GoogleAuthProvider();

async function signInWithGoogle() {
  try {
    const result = await signInWithPopup(auth, googleProvider);
    return result;
  } catch (error) {
    console.error("Google Sign In Error:", error);
    throw error;
  }
}

console.log("Firebase Auth Initialized:", auth);
console.log("Firebase Config:", firebaseConfig);

export {
  auth,
  signInWithEmailAndPassword,
  createUserWithEmailAndPassword,
  onAuthStateChanged,
  signOut,
  signInWithGoogle,
  deleteUser,
  sendEmailVerification
};
