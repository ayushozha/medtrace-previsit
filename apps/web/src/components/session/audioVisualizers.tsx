import { useCallback, useEffect, useRef } from 'react';

/** Decode a `data:` audio URL into a Blob the visualiser can read. */
export function base64ToBlob(base64Str: string): Blob | null {
  if (!base64Str) return null;
  try {
    const parts = base64Str.split(',');
    const mime = parts[0].match(/:(.*?);/)?.[1] || 'audio/wav';
    const bstr = atob(parts[1] || parts[0]);
    let n = bstr.length;
    const u8arr = new Uint8Array(n);
    while (n--) u8arr[n] = bstr.charCodeAt(n);
    return new Blob([u8arr], { type: mime });
  } catch (err) {
    console.error('Failed to parse base64 audio:', err);
    return null;
  }
}

function audioContext(): AudioContext {
  const Ctor =
    window.AudioContext ?? (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
  return new Ctor();
}

interface AudioVisualizerProps {
  blob: Blob;
  width: number;
  height: number;
  barWidth?: number;
  gap?: number;
  barColor?: string;
  barPlayedColor?: string;
  currentTime?: number;
}

/** Static peak-bar waveform for recorded audio, with a played/unplayed split. */
export function AudioVisualizer({
  blob,
  width,
  height,
  barWidth = 2,
  gap = 1.5,
  barColor = '#dadce0',
  barPlayedColor = '#f29900',
  currentTime = 0,
}: AudioVisualizerProps) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const peaksRef = useRef<number[]>([]);
  const durationRef = useRef<number>(0);

  const draw = useCallback(() => {
    const canvas = canvasRef.current;
    if (!canvas || peaksRef.current.length === 0) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    ctx.clearRect(0, 0, width, height);
    const numBars = peaksRef.current.length;
    const progress = durationRef.current > 0 ? currentTime / durationRef.current : 0;
    const playedBars = Math.floor(progress * numBars);

    peaksRef.current.forEach((peak, i) => {
      const x = i * (barWidth + gap);
      const barHeight = peak * height * 0.9 + 2; // keep a 2px floor so silence stays visible
      ctx.fillStyle = i < playedBars ? barPlayedColor : barColor;
      ctx.beginPath();
      ctx.roundRect(x, height / 2 - barHeight / 2, barWidth, barHeight, 4);
      ctx.fill();
    });
  }, [barColor, barPlayedColor, barWidth, currentTime, gap, height, width]);

  useEffect(() => {
    if (!blob) return;
    let cancelled = false;

    void (async () => {
      try {
        const arrayBuffer = await blob.arrayBuffer();
        const ctx = audioContext();
        const audioBuffer = await ctx.decodeAudioData(arrayBuffer);
        durationRef.current = audioBuffer.duration;
        await ctx.close();
        if (cancelled) return;

        const numBars = Math.floor(width / (barWidth + gap));
        const rawData = audioBuffer.getChannelData(0);
        const blockSize = Math.floor(rawData.length / numBars);
        const peaks: number[] = [];
        for (let i = 0; i < numBars; i++) {
          let max = 0;
          for (let j = 0; j < blockSize; j++) {
            const val = Math.abs(rawData[i * blockSize + j]);
            if (val > max) max = val;
          }
          peaks.push(max);
        }
        peaksRef.current = peaks;
        draw();
      } catch (err) {
        console.error('Error decoding audio for visualizer:', err);
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [blob, width, barWidth, gap, draw]);

  useEffect(() => {
    draw();
  }, [draw]);

  return <canvas ref={canvasRef} width={width} height={height} />;
}

interface LiveAudioVisualizerProps {
  mediaRecorder: MediaRecorder;
  width: number;
  height: number;
  barColor?: string;
}

/** Live FFT bars driven by the active MediaRecorder stream. */
export function LiveAudioVisualizer({
  mediaRecorder,
  width,
  height,
  barColor = '#0b57d0',
}: LiveAudioVisualizerProps) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const animationFrameRef = useRef<number | null>(null);

  useEffect(() => {
    if (!mediaRecorder?.stream) return;

    let ctxAudio: AudioContext | null = null;
    try {
      ctxAudio = audioContext();
      const source = ctxAudio.createMediaStreamSource(mediaRecorder.stream);
      const analyser = ctxAudio.createAnalyser();
      analyser.fftSize = 64;
      source.connect(analyser);

      const bufferLength = analyser.frequencyBinCount;
      const dataArray = new Uint8Array(bufferLength);

      const draw = () => {
        const canvas = canvasRef.current;
        const ctx = canvas?.getContext('2d');
        if (!canvas || !ctx) return;

        animationFrameRef.current = requestAnimationFrame(draw);
        analyser.getByteFrequencyData(dataArray);
        ctx.clearRect(0, 0, width, height);

        const barWidth = (width / bufferLength) * 0.8;
        let x = 0;
        for (let i = 0; i < bufferLength; i++) {
          const barHeight = (dataArray[i] / 255) * height * 0.85 + 2;
          ctx.fillStyle = barColor;
          ctx.beginPath();
          ctx.roundRect(x, height / 2 - barHeight / 2, barWidth - 1, barHeight, 4);
          ctx.fill();
          x += barWidth + 1;
        }
      };

      draw();
    } catch (err) {
      console.error('Failed to set up LiveAudioVisualizer:', err);
    }

    return () => {
      if (animationFrameRef.current) cancelAnimationFrame(animationFrameRef.current);
      void ctxAudio?.close();
    };
  }, [mediaRecorder, width, height, barColor]);

  return <canvas ref={canvasRef} width={width} height={height} />;
}
