"""Diskreter Kalman-Filter für Position/Geschwindigkeit in lokalen Feldmetern.

Je Achse Zustand [Ort, Geschwindigkeit], Beschleunigung als Prozessrauschen.
Vorhersage und Messkorrektur nach Welch/Bishop; keine GNSS-Freigabe wird ersetzt.
"""
import math

class _Achse:
    def __init__(self,x,v,var):self.x=x;self.v=v;self.p=var;self.c=0.;self.q=0.25
    def predict(self,dt,a):
        self.x+=self.v*dt
        self.p+=2*dt*self.c+dt*dt*self.q+a*a*dt**4/4
        self.c+=dt*self.q+a*a*dt**3/2
        self.q+=a*a*dt*dt
    def messen(self,x,v,var):
        # Positionsmessung, danach unabhängige Geschwindigkeitsmessung.
        s=self.p+var;k=self.p/s;l=self.c/s;rest=x-self.x
        self.x+=k*rest;self.v+=l*rest
        self.q-=l*self.c;self.c*=1-k;self.p*=1-k
        s=self.q+0.25;k=self.c/s;l=self.q/s;rest=v-self.v
        self.x+=k*rest;self.v+=l*rest
        self.p=max(1e-12,self.p-k*self.c);self.c*=1-l;self.q=max(1e-12,self.q*(1-l))

class Positionsfilter:
    def __init__(self):self.reset()
    def reset(self):
        self.achsen=None;self.zeit=None;self.qualitaet=None;self.akzeptiert=True
        self.ausreisser=0;self.innovation_m=0.;self.folge=0
    def update(self,position,speed_ms,kurs,zeit,qualitaet,sigma_m=None,beschleunigung=2.):
        if not all(math.isfinite(x) for x in (*position,speed_ms,kurs,zeit,beschleunigung)):
            raise ValueError("Positionsfilter braucht endliche Messwerte")
        sigma=sigma_m if sigma_m is not None else {4:.03,5:.5,2:1.5}.get(qualitaet,3.)
        if not math.isfinite(sigma) or sigma<=0:sigma=3.
        sigma=max(.01,min(100.,sigma));var=sigma*sigma
        h=math.radians(kurs);vx,vy=math.sin(h)*speed_ms,math.cos(h)*speed_ms
        dt=zeit-self.zeit if self.zeit is not None else 0
        if (self.achsen is None or not 0<dt<=1.0 or qualitaet!=self.qualitaet or self.folge>=3):
            self.achsen=[_Achse(position[0],vx,var),_Achse(position[1],vy,var)]
            self.folge=0;self.akzeptiert=True;self.innovation_m=0
        else:
            for achse in self.achsen:achse.predict(dt,beschleunigung)
            rest=[p-a.x for p,a in zip(position,self.achsen)]
            self.innovation_m=math.hypot(*rest)
            abstand=sum(r*r/(a.p+var) for r,a in zip(rest,self.achsen))
            self.akzeptiert=abstand<=36 or self.innovation_m<=.25
            if self.akzeptiert:
                self.folge=0
                for a,p,v in zip(self.achsen,position,(vx,vy)):a.messen(p,v,var)
            else:self.folge+=1;self.ausreisser+=1
        self.zeit=zeit;self.qualitaet=qualitaet
        return tuple(a.x for a in self.achsen)
    def status(self):
        return {"akzeptiert":self.akzeptiert,"ausreisser":self.ausreisser,
                "innovation_m":round(self.innovation_m,3),
                "sigma_m":math.sqrt(sum(a.p for a in self.achsen)) if self.achsen else None}
