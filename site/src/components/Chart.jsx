import React, { useEffect, useRef } from 'react';
import * as echarts from 'echarts';

export default function Chart({ option, className = '', onInit }) {
  const ref = useRef(null);

  useEffect(() => {
    if (!ref.current) return;
    const chart = echarts.init(ref.current, null, { renderer: 'canvas' });
    chart.setOption(option, true);
    const cleanupInit = onInit?.(chart, ref.current);
    const resize = () => chart.resize();
    window.addEventListener('resize', resize);
    const observer = new ResizeObserver(resize);
    observer.observe(ref.current);
    return () => {
      observer.disconnect();
      window.removeEventListener('resize', resize);
      if (typeof cleanupInit === 'function') cleanupInit();
      chart.dispose();
    };
  }, [option, onInit]);

  return <div ref={ref} className={`chart ${className}`} />;
}
