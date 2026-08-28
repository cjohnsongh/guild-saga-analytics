import React from 'react';
import Chart from './Chart.jsx';

export function Card({ title, subtitle, span = 2, className = '', children, footer = true }) {
  return (
    <section className={`card span-${span} ${className}`}>
      {(title || subtitle) && (
        <header className="card-head">
          {title && <div className="card-title">{title}</div>}
          {subtitle && <div className="card-subtitle">{subtitle}</div>}
        </header>
      )}
      <div className="card-body">{children}</div>
      {footer && <div className="card-footer"><span>Guild Saga Analytics</span><span>Aug 26, 2026</span></div>}
    </section>
  );
}

export function KpiCard({ title, value, unit, span = 2 }) {
  return (
    <Card title={title} span={span} className="kpi-card">
      <div className="kpi-ring">
        <div className="kpi-value">{value}</div>
        {unit && <div className="kpi-unit">{unit}</div>}
      </div>
    </Card>
  );
}

export function ChartCard({ title, subtitle, span = 4, option, className = '' }) {
  return (
    <Card title={title} subtitle={subtitle} span={span} className={`chart-card ${className}`}>
      <Chart option={option} />
    </Card>
  );
}

export function SectionBanner({ title, children }) {
  return (
    <section className="section-banner span-12">
      <div className="section-banner-title">{title}</div>
      {children && <div className="section-banner-copy">{children}</div>}
    </section>
  );
}
