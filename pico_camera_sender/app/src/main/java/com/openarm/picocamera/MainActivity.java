package com.openarm.picocamera;

import android.Manifest;
import android.app.Activity;
import android.content.Context;
import android.content.pm.PackageManager;
import android.graphics.Color;
import android.hardware.camera2.CameraAccessException;
import android.hardware.camera2.CameraCaptureSession;
import android.hardware.camera2.CameraCharacteristics;
import android.hardware.camera2.CameraDevice;
import android.hardware.camera2.CameraManager;
import android.hardware.camera2.CaptureRequest;
import android.media.Image;
import android.media.ImageReader;
import android.os.Bundle;
import android.os.Handler;
import android.os.HandlerThread;
import android.os.PowerManager;
import android.util.Log;
import android.util.Size;
import android.view.Gravity;
import android.view.ViewGroup;
import android.widget.ScrollView;
import android.widget.TextView;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;
import java.util.Set;

public final class MainActivity extends Activity {
    private static final String TAG = "OpenArmCameraProbe";
    private static final int CAMERA_PERMISSION_REQUEST = 1001;
    private static final List<String> CANDIDATE_IDS = Arrays.asList("4", "5", "8");

    private final List<String> pendingCameraIds = new ArrayList<>();
    private TextView output;
    private CameraManager cameraManager;
    private HandlerThread cameraThread;
    private Handler cameraHandler;
    private CameraDevice activeCamera;
    private CameraCaptureSession activeSession;
    private ImageReader imageReader;
    private PowerManager.WakeLock wakeLock;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        output = new TextView(this);
        output.setTextColor(Color.WHITE);
        output.setTextSize(18);
        output.setPadding(32, 24, 32, 24);
        output.setGravity(Gravity.START);
        ScrollView scroll = new ScrollView(this);
        scroll.addView(output, new ScrollView.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT));
        setContentView(scroll);

        PowerManager powerManager = (PowerManager) getSystemService(POWER_SERVICE);
        wakeLock = powerManager.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK,
                "OpenArm:PicoCameraProbe");
        wakeLock.acquire(10 * 60 * 1000L);

        cameraThread = new HandlerThread("pico-camera-probe");
        cameraThread.start();
        cameraHandler = new Handler(cameraThread.getLooper());
        cameraManager = (CameraManager) getSystemService(Context.CAMERA_SERVICE);

        append("OpenArm PICO 4 Ultra camera probe");
        append("Android " + android.os.Build.VERSION.RELEASE
                + " / " + android.os.Build.MODEL);
        if (checkSelfPermission(Manifest.permission.CAMERA)
                == PackageManager.PERMISSION_GRANTED) {
            runProbe();
        } else {
            requestPermissions(new String[]{Manifest.permission.CAMERA},
                    CAMERA_PERMISSION_REQUEST);
        }
    }

    @Override
    public void onRequestPermissionsResult(
            int requestCode, String[] permissions, int[] grantResults) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults);
        if (requestCode == CAMERA_PERMISSION_REQUEST
                && grantResults.length > 0
                && grantResults[0] == PackageManager.PERMISSION_GRANTED) {
            runProbe();
        } else {
            append("ERROR camera permission denied");
        }
    }

    private void runProbe() {
        try {
            String[] visibleIds = cameraManager.getCameraIdList();
            append("Camera2 visible IDs: " + Arrays.toString(visibleIds));
            for (String id : CANDIDATE_IDS) {
                inspectCharacteristics(id);
                pendingCameraIds.add(id);
            }
            probeNextCamera();
        } catch (CameraAccessException error) {
            append("ERROR enumerating cameras: " + error);
        }
    }

    private void inspectCharacteristics(String id) {
        try {
            CameraCharacteristics characteristics =
                    cameraManager.getCameraCharacteristics(id);
            Integer facing = characteristics.get(CameraCharacteristics.LENS_FACING);
            Set<String> physicalIds = characteristics.getPhysicalCameraIds();
            android.hardware.camera2.params.StreamConfigurationMap map =
                    characteristics.get(
                            CameraCharacteristics.SCALER_STREAM_CONFIGURATION_MAP);
            Size[] yuvSizes = map == null ? null
                    : map.getOutputSizes(android.graphics.ImageFormat.YUV_420_888);
            append("ID " + id + " characteristics: facing=" + facing
                    + " physical=" + physicalIds
                    + " YUV=" + summarizeSizes(yuvSizes));
        } catch (Exception error) {
            append("ID " + id + " characteristics ERROR: " + error);
        }
    }

    private String summarizeSizes(Size[] sizes) {
        if (sizes == null) return "none";
        StringBuilder result = new StringBuilder();
        int limit = Math.min(sizes.length, 12);
        for (int index = 0; index < limit; index++) {
            if (index > 0) result.append(", ");
            result.append(sizes[index]);
        }
        if (sizes.length > limit) result.append(" …(").append(sizes.length).append(")");
        return result.toString();
    }

    private void probeNextCamera() {
        closeActiveCamera();
        if (pendingCameraIds.isEmpty()) {
            append("PROBE COMPLETE");
            return;
        }
        String cameraId = pendingCameraIds.remove(0);
        append("Opening camera " + cameraId + " at 640x480 YUV…");
        imageReader = ImageReader.newInstance(
                640, 480, android.graphics.ImageFormat.YUV_420_888, 2);
        imageReader.setOnImageAvailableListener(reader -> {
            Image image = reader.acquireLatestImage();
            if (image == null) return;
            long timestamp = image.getTimestamp();
            int planes = image.getPlanes().length;
            image.close();
            append("ID " + cameraId + " FRAME timestamp=" + timestamp
                    + " planes=" + planes);
            cameraHandler.postDelayed(this::probeNextCamera, 150);
        }, cameraHandler);
        try {
            cameraManager.openCamera(cameraId, new CameraDevice.StateCallback() {
                @Override
                public void onOpened(CameraDevice camera) {
                    activeCamera = camera;
                    createCaptureSession(cameraId, camera);
                }

                @Override
                public void onDisconnected(CameraDevice camera) {
                    append("ID " + cameraId + " DISCONNECTED");
                    camera.close();
                    cameraHandler.postDelayed(MainActivity.this::probeNextCamera, 150);
                }

                @Override
                public void onError(CameraDevice camera, int error) {
                    append("ID " + cameraId + " OPEN ERROR=" + error);
                    camera.close();
                    cameraHandler.postDelayed(MainActivity.this::probeNextCamera, 150);
                }
            }, cameraHandler);
        } catch (Exception error) {
            append("ID " + cameraId + " OPEN EXCEPTION: " + error);
            cameraHandler.postDelayed(this::probeNextCamera, 150);
        }
    }

    private void createCaptureSession(String cameraId, CameraDevice camera) {
        try {
            camera.createCaptureSession(
                    List.of(imageReader.getSurface()),
                    new CameraCaptureSession.StateCallback() {
                        @Override
                        public void onConfigured(CameraCaptureSession session) {
                            activeSession = session;
                            try {
                                CaptureRequest.Builder request = camera.createCaptureRequest(
                                        CameraDevice.TEMPLATE_RECORD);
                                request.addTarget(imageReader.getSurface());
                                request.set(CaptureRequest.CONTROL_AE_TARGET_FPS_RANGE,
                                        new android.util.Range<>(30, 30));
                                session.setRepeatingRequest(request.build(), null, cameraHandler);
                            } catch (Exception error) {
                                append("ID " + cameraId + " CAPTURE ERROR: " + error);
                                cameraHandler.postDelayed(
                                        MainActivity.this::probeNextCamera, 150);
                            }
                        }

                        @Override
                        public void onConfigureFailed(CameraCaptureSession session) {
                            append("ID " + cameraId + " SESSION CONFIG FAILED");
                            cameraHandler.postDelayed(MainActivity.this::probeNextCamera, 150);
                        }
                    }, cameraHandler);
        } catch (Exception error) {
            append("ID " + cameraId + " SESSION EXCEPTION: " + error);
            cameraHandler.postDelayed(this::probeNextCamera, 150);
        }
    }

    private void closeActiveCamera() {
        if (activeSession != null) {
            activeSession.close();
            activeSession = null;
        }
        if (activeCamera != null) {
            activeCamera.close();
            activeCamera = null;
        }
        if (imageReader != null) {
            imageReader.close();
            imageReader = null;
        }
    }

    private void append(String message) {
        Log.i(TAG, message);
        runOnUiThread(() -> output.append(message + "\n"));
    }

    @Override
    protected void onDestroy() {
        closeActiveCamera();
        if (cameraThread != null) cameraThread.quitSafely();
        if (wakeLock != null && wakeLock.isHeld()) wakeLock.release();
        super.onDestroy();
    }
}
