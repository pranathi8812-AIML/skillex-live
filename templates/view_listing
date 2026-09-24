@app.route('/listing/<int:id>/edit', methods=['GET', 'POST'])
@login_required
def edit_listing(id):
    listing = Listing.query.get_or_404(id)
    
    # Security check: Only the creator can edit this listing
    if listing.user_id != current_user.id:
        return redirect(url_for('view_listing', id=id))
        
    if request.method == 'POST':
        listing.type = request.form.get('type')
        listing.category = request.form.get('category')
        listing.title = request.form.get('title')
        listing.description = request.form.get('description')
        listing.credits = int(request.form.get('credits'))
        listing.ward = request.form.get('ward')
        
        db.session.commit()
        return redirect(url_for('view_listing', id=listing.id))
        
    return render_template('edit_listing.html', listing=listing)

@app.route('/listing/<int:id>/delete')
@login_required
def delete_listing(id):
    listing = Listing.query.get_or_404(id)
    
    # Security check: Only the creator can delete this listing
    if listing.user_id == current_user.id:
        db.session.delete(listing)
        db.session.commit()
        
    return redirect(url_for('dashboard'))