# 5G Edge Network Queue Management

Welcome to the **5G Edge-AI Queue Management** project! 

This system uses Computer Vision (Artificial Intelligence) to monitor video cameras in real-time. It detects crowds of people, tracks how long individuals stand within a specific line (a "Region of Interest"), and statistically calculates live wait times and crowd density. 

Designed for low latency, it showcases how AI applications can seamlessly broadcast high-speed real-time video directly out to web-client dashboards without lag using ultra-fast WebSockets.

Whether you are a developer, a student, or a logistics manager, this guide is written step-by-step so **anyone** can install and run this application on their computer.

---

## 🏗️ How The Architecture Works (Under the Hood)

This system is built from the ground up to eliminate all computational bottlenecks through decoupled threading and hardware acceleration:

### 1. Producer-Consumer Asynchronous Video Queues
- **The Video Producer:** A high-speed background thread exclusively grabs webcam footage and shoves it over the WebSocket encoded as purely compressed binaries. Because it does no math, it runs perfectly fast natively.
- **The AI Consumer:** A secondary async thread monitors a shared lock. When the GPU is ready, it grabs the freshest frame, processes the geometry, and throws the coordinates into a JSON telemetry package. This creates **natural frame skipping**, isolating your live view from backend algorithmic calculations!

### 2. Pure Client-Side GPU Rendering
- We stripped out all heavy Python OpenCV (`cv2`) image rendering. 
- The Python Edge engine calculates pure coordinate geometry and fires lightweight metadata down the WebSocket. 
- Your browser's Javascript engine parses these JSON arrays and commands the HTML5 `<canvas>` to Native-draw the tracking rectangles on the client's graphics engine. It perfectly scales and syncs transparently over the live video frames at 0ms latency.

### 3. NVIDIA TensorRT / CUDA Acceleration
- We swapped PyTorch tensors out in favor of the **ONNX-RUNTIME-GPU** package.
- The Engine natively detects the internal GPU and aggressively hijacks CUDA/Tensor Cores to parallelize YOLO object detection, slashing AI prediction times down by roughly 30%.

---

## 🛠️ Step 1: Pre-requisites (What you need installed)

Before running the application, make sure your computer has the following:

1. **A Webcam or IP Camera**: The application needs to "see" a video feed. By default, it will attempt to use your computer's built-in webcam.
2. **Python (version 3.10 to 3.12)**: This is the programming language that powers the AI. 
   - [Download Python Here](https://www.python.org/downloads/)
   - *Crucial Windows Note*: When you install Python, make absolutely sure to check the box that says **"Add Python to PATH"** at the bottom of the installation window.

---

## 💻 Step 2: Setting up your Environment

We use a "Virtual Environment" to install the project's robotics and AI dependencies so they don't mess with the rest of your computer.

1. **Open your Terminal**:
   - On Windows, press the `Windows Key`, type `PowerShell`, and hit Enter.
   - Use the `cd` command to navigate to the exact folder where you saved this project.
     ```powershell
     cd C:\Users\Ojas\Desktop\5g-usecase
     ```

2. **Create the Virtual Environment**:
   Run the following command exactly as written. This creates an isolated folder named `venv`.
   ```powershell
   python -m venv venv
   ```

3. **Activate the Virtual Environment**:
   Every time you want to run this application, you must activate the environment first. 
   ```powershell
   .\venv\Scripts\activate
   ```
   *(You should now see `(venv)` appear at the start of your terminal line).*

   > **Troubleshooting Windows Errors**: If PowerShell gives you an error about "running scripts is disabled on this system", run this command to fix your permissions: `Set-ExecutionPolicy Unrestricted -Scope CurrentUser`, then try activating again.

4. **Install the Requirements**:
   Now, tell Python to download Artificial Intelligence plugins (like PyTorch and YOLO) and the web server dependencies. This might take a few minutes!
   ```powershell
   pip install -r requirements.txt
   ```

---

## ⚙️ Step 3: Configuring Your Camera Tracking Area

Before starting the engine, we need to map the AI to look at the correct position in your room/store. Open the `config.yaml` file in any text editor (like Notepad or VS Code).

Here is what you need to change:

- **`video_source`**: 
  - Leave it as `0` if you want to use your laptop webcam.
  - If you bought a professional IP Security Camera, replace the `0` with the camera's network stream link. Example: `"rtsp://admin:password123@192.168.1.51:554/stream1"`
- **`queue_wait_threshold_sec`**: 
  - Set this to `5.0`. This implies a person must stand in line for 5 full seconds before the AI registers them as "waiting in queue" (preventing people casually walking by from ruining the statistics).
- **`roi_polygon`**: 
  - This is the "Box" drawn over your video. Only people inside this coordinate box will be tracked. You map this via 4 `[x, y]` pixel coordinates corresponding to the Top-Left, Top-Right, Bottom-Right, and Bottom-Left of the line area.

---

## 🚀 Step 4: Running the Application

You are finished with the setup! It's time to start the AI engine and the dashboard.

1. Ensure your terminal is still inside the project folder and the `(venv)` tag is active.
2. Start the AI Server:
   ```powershell
   python app.py
   ```
3. Look at the terminal output. It will inform you that the Neural Network has compiled onto an `ONNX` graph, and the Edge Analytics Node has started successfully.

---

## 📈 Step 5: View the Web Dashboard

Once the terminal confirms the server is running, you can view the live results!

1. Open **Google Chrome, Firefox, or Edge**.
2. Type the following address into your URL bar and hit enter:
   **`http://localhost:5000`**

You will be greeted by the **Real-Time Edge Analytics Dashboard**. 
Here you can:
- Watch the live camera feed with intelligent AI bounding boxes tracking individuals.
- Monitor the **Historical Density Graph** drawing in real-time.
- View the exact estimated Wait Time limits calculated by our mathematical estimators.
- Simulate random 5G internet outages clicking the Dropdown simulator options and viewing how the ultra-fast WebSocket stream manages to stay stable!
