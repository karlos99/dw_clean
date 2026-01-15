from django.shortcuts import render, redirect

# Create your views here.
def index(request):
    if request.user.is_authenticated:
        # Check if user has a role and if it is "Project Admin"
        if request.user.role and request.user.role.name == 'Project Admin':
            return redirect('users:manage_users')
            
    return render(request, 'landing.html')

